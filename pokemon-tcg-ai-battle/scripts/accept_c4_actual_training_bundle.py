"""Fail-closed consumer acceptance for a C4 actual-training bundle.

The command never collects or invents cabt data.  It only accepts a bundle
already produced by data-ops, then delegates training to the existing Student
CLIs after every data, split, provenance, and privacy check has passed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from mage_ptcg.student.artifact import feature_schema, load_validated_artifact
from mage_ptcg.student.dataset import DATASET_SCHEMA_VERSION, DatasetValidationError, RuleBCExample
from mage_ptcg.student.model import MODEL_FEATURE_DIM, example_matrix
from mage_ptcg.meta_specialist.cabt_json_contract_v1 import is_ordered_selection


BUNDLE_SCHEMA_VERSION = "c4-actual-training-bundle-v1"
SPLIT_SCHEMA_VERSION = "c4-actual-episode-split-v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_WINDOWS_ABSOLUTE = re.compile(r"^[A-Za-z]:[\\\\/]")


class BundleAcceptanceError(ValueError):
    """Raised when a bundle is incomplete, inconsistent, or unsafe."""


@dataclass(frozen=True, slots=True)
class AcceptedBundle:
    root: Path
    dataset_path: Path
    dataset_manifest_path: Path
    split_manifest_path: Path
    public_summary_path: Path
    examples: tuple[RuleBCExample, ...]
    dataset_manifest: dict[str, object]
    split_manifest: dict[str, object]
    public_summary: dict[str, object]
    dataset_hash: str
    dataset_manifest_hash: str
    split_manifest_hash: str
    source_split_hash: str

    @property
    def is_actual_trained(self) -> bool:
        return self.dataset_manifest["artifact_purpose"] in {"ACTUAL_TRAINING", "ACTUAL_TRAINED"}

    def public_result(self) -> dict[str, object]:
        return {
            "accepted": True,
            "artifact_purpose": self.dataset_manifest["artifact_purpose"],
            "performance_eligible": self.dataset_manifest["performance_eligible"],
            "dataset_hash": self.dataset_hash,
            "dataset_manifest_hash": self.dataset_manifest_hash,
            "split_manifest_hash": self.split_manifest_hash,
            "source_split_hash": self.source_split_hash,
            "episode_count": self.dataset_manifest["episode_count"],
            "decision_count": self.dataset_manifest["decision_count"],
            "candidate_count": self.dataset_manifest["candidate_count"],
            "train_episode_count": self.split_manifest["train_episode_count"],
            "validation_episode_count": self.split_manifest["validation_episode_count"],
            "split_overlap_count": self.split_manifest["split_overlap_count"],
        }


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _content_hash(value: object) -> str:
    return _sha256_bytes(_canonical_json(value).encode("utf-8"))


def _read_json(path: Path) -> dict[str, object]:
    if not path.is_file() or path.is_symlink():
        raise BundleAcceptanceError("required bundle file is missing or unsafe")
    try:
        value = json.loads(path.read_text(encoding="utf-8"), parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("non-finite JSON")))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise BundleAcceptanceError("bundle JSON is malformed") from exc
    if not isinstance(value, dict):
        raise BundleAcceptanceError("bundle JSON must be an object")
    _require_finite(value)
    return value


def _read_jsonl(path: Path) -> tuple[list[dict[str, object]], tuple[RuleBCExample, ...]]:
    if not path.is_file() or path.is_symlink():
        raise BundleAcceptanceError("rule-bc-v1 dataset is missing or unsafe")
    rows: list[dict[str, object]] = []
    examples: list[RuleBCExample] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise BundleAcceptanceError("rule-bc-v1 dataset is unreadable") from exc
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line, parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("non-finite JSON")))
            if not isinstance(row, dict):
                raise ValueError("row is not an object")
            _require_finite(row)
            example = RuleBCExample.from_dict(row)
        except (ValueError, json.JSONDecodeError, DatasetValidationError) as exc:
            raise BundleAcceptanceError(f"dataset JSONL row {line_number} is invalid") from exc
        rows.append(row)
        examples.append(example)
    if not examples:
        raise BundleAcceptanceError("rule-bc-v1 dataset is empty")
    return rows, tuple(examples)


def _require_finite(value: object) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise BundleAcceptanceError("bundle contains a non-finite value")
    if isinstance(value, Mapping):
        for child in value.values():
            _require_finite(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _require_finite(child)


def _require_sha256(value: object, field: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise BundleAcceptanceError(f"{field} must be a sha256")
    return value


def _require_positive_int(value: object, field: str) -> int:
    if type(value) is not int or value <= 0:
        raise BundleAcceptanceError(f"{field} must be positive")
    return int(value)


def _require_zero(value: object, field: str) -> None:
    if type(value) is not int or value != 0:
        raise BundleAcceptanceError(f"{field} must be zero")


def _manifest_hash(value: Mapping[str, object]) -> str:
    payload = dict(value)
    payload.pop("manifest_hash", None)
    return _content_hash(payload)


def _contains_absolute_path(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(_contains_absolute_path(child) for child in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_absolute_path(child) for child in value)
    return isinstance(value, str) and (value.startswith("/") or bool(_WINDOWS_ABSOLUTE.match(value)))


def _relative_bundle_file(root: Path, value: object, field: str) -> Path:
    if not isinstance(value, str) or not value or Path(value).is_absolute() or ".." in Path(value).parts:
        raise BundleAcceptanceError(f"{field} must be a relative bundle path")
    candidate = root / value
    if not candidate.is_file() or candidate.is_symlink():
        raise BundleAcceptanceError(f"{field} does not resolve to a regular file")
    return candidate


def _validate_feature_schema(manifest: Mapping[str, object]) -> None:
    expected = feature_schema()
    for field in ("feature_schema_version", "feature_schema_hash", "feature_dimension", "state_feature_dimension", "action_feature_dimension"):
        if manifest.get(field) != expected[field]:
            raise BundleAcceptanceError("feature schema mismatch")


def _validate_rows(examples: Sequence[RuleBCExample]) -> tuple[set[str], int, int]:
    source_ids: set[str] = set()
    candidate_count = 0
    chosen_decisions = 0
    for example in examples:
        if example.schema_version != DATASET_SCHEMA_VERSION:
            raise BundleAcceptanceError("dataset schema mismatch")
        source_ids.add(example.source_id)
        candidate_count += len(example.legal_actions)
        indexes = {action["digest"]: index for index, action in enumerate(example.legal_actions)}
        if not example.target_action_digests:
            raise BundleAcceptanceError("chosen target is missing")
        if any(target not in indexes or not 0 <= indexes[target] < len(example.legal_actions) for target in example.target_action_digests):
            raise BundleAcceptanceError("chosen target index is invalid")
        matrix, targets = example_matrix(example)
        if len(matrix) != len(example.legal_actions) or not targets:
            raise BundleAcceptanceError("candidate feature matrix is invalid")
        if any(len(row) != MODEL_FEATURE_DIM or any(not math.isfinite(value) for value in row) for row in matrix):
            raise BundleAcceptanceError("candidate features have an invalid dimension or non-finite value")
        chosen_decisions += 1
    if not source_ids or candidate_count <= 0 or chosen_decisions != len(examples):
        raise BundleAcceptanceError("dataset counts are invalid")
    return source_ids, candidate_count, chosen_decisions


def _validate_split(split: Mapping[str, object], *, source_ids: set[str], dataset_hash: str) -> tuple[str, str]:
    if split.get("schema_version") != SPLIT_SCHEMA_VERSION:
        raise BundleAcceptanceError("split schema mismatch")
    if split.get("dataset_hash") != dataset_hash:
        raise BundleAcceptanceError("split dataset hash mismatch")
    if not isinstance(split.get("split_method"), str) or not split["split_method"]:
        raise BundleAcceptanceError("split method is missing")
    assignments = split.get("assignments")
    if not isinstance(assignments, dict) or set(assignments) != source_ids:
        raise BundleAcceptanceError("split does not cover exactly the dataset episode groups")
    if any(value not in {"train", "validation"} for value in assignments.values()):
        raise BundleAcceptanceError("split assignment is invalid")
    train = {key for key, value in assignments.items() if value == "train"}
    validation = {key for key, value in assignments.items() if value == "validation"}
    if not train or not validation:
        raise BundleAcceptanceError("train and validation episodes are required")
    if train.intersection(validation):
        raise BundleAcceptanceError("split overlap detected")
    if split.get("train_episode_count") != len(train) or split.get("validation_episode_count") != len(validation):
        raise BundleAcceptanceError("split episode counts mismatch")
    _require_zero(split.get("split_overlap_count"), "split_overlap_count")
    _require_zero(split.get("duplicate_episode_count"), "duplicate_episode_count")
    _require_zero(split.get("duplicate_decision_count"), "duplicate_decision_count")
    manifest_hash = _require_sha256(split.get("manifest_hash"), "split manifest_hash")
    if manifest_hash != _manifest_hash(split):
        raise BundleAcceptanceError("split manifest hash mismatch")
    source_split_hash = _require_sha256(split.get("split_hash"), "split_hash")
    expected_split_hash = _content_hash({"assignments": dict(sorted(assignments.items())), "dataset_hash": dataset_hash})
    if source_split_hash != expected_split_hash:
        raise BundleAcceptanceError("split hash mismatch")
    return manifest_hash, source_split_hash


def _validate_private_binding(root: Path, manifest: Mapping[str, object], examples: Sequence[RuleBCExample]) -> None:
    binding = manifest.get("private_binding")
    if binding is None:
        return
    if not isinstance(binding, Mapping):
        raise BundleAcceptanceError("private binding declaration is invalid")
    binding_hash = _require_sha256(binding.get("sha256"), "private binding sha256")
    binding_count = _require_positive_int(binding.get("record_count"), "private binding record_count")
    if binding.get("trainer_input") is not False:
        raise BundleAcceptanceError("private binding must not be trainer input")
    if "path" not in binding:
        if binding.get("path_role") != "private_bindings":
            raise BundleAcceptanceError("private binding path role is invalid")
        return
    path = _relative_bundle_file(root, binding.get("path"), "private binding path")
    if _sha256_bytes(path.read_bytes()) != binding_hash:
        raise BundleAcceptanceError("private binding hash mismatch")
    records, _unused = _read_jsonl_records(path)
    if binding_count != len(records):
        raise BundleAcceptanceError("private binding count mismatch")
    by_decision: dict[tuple[str, int], Mapping[str, object]] = {}
    for record in records:
        group, index = record.get("episode_group_id"), record.get("decision_index")
        if not isinstance(group, str) or type(index) is not int or (group, index) in by_decision:
            raise BundleAcceptanceError("private binding has invalid or duplicate decision identity")
        by_decision[group, index] = record
    if len(by_decision) != len(examples):
        raise BundleAcceptanceError("private binding count does not match decisions")
    for example in examples:
        group, index = example.metadata.get("episode_group_id"), example.metadata.get("decision_index")
        if not isinstance(group, str) or not isinstance(index, str) or not index.isdigit():
            raise BundleAcceptanceError("dataset example is missing private binding identity")
        record = by_decision.get((group, int(index)))
        if record is None:
            raise BundleAcceptanceError("private binding target does not match dataset decision")
        candidates = record.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise BundleAcceptanceError("private binding candidates are invalid")
        if record.get("legal_candidate_count") != len(candidates):
            raise BundleAcceptanceError("private binding candidate count is invalid")
        candidate_digests: dict[int, str] = {}
        for candidate in candidates:
            if not isinstance(candidate, Mapping):
                raise BundleAcceptanceError("private binding candidate is invalid")
            option_index = candidate.get("option_index")
            candidate_digest = candidate.get("digest")
            if (
                type(option_index) is not int
                or option_index < 0
                or not isinstance(candidate_digest, str)
                or not _SHA256.fullmatch(candidate_digest)
                or option_index in candidate_digests
            ):
                raise BundleAcceptanceError("private binding candidate is invalid")
            candidate_digests[option_index] = candidate_digest
        if set(candidate_digests) != set(range(len(candidates))):
            raise BundleAcceptanceError("private binding candidate indices are invalid")
        chosen_indices = record.get("chosen_option_indices")
        chosen_digests = record.get("chosen_action_digests")
        teacher_digests = record.get("teacher_chosen_action_digests")
        if (
            not isinstance(chosen_indices, list)
            or not isinstance(chosen_digests, list)
            or not isinstance(teacher_digests, list)
            or any(type(value) is not int for value in chosen_indices)
            or any(not isinstance(value, str) or not _SHA256.fullmatch(value) for value in [*chosen_digests, *teacher_digests])
            or len(chosen_indices) != len(set(chosen_indices))
            or any(value not in candidate_digests for value in chosen_indices)
        ):
            raise BundleAcceptanceError("private binding target does not match dataset decision")
        try:
            ordered = is_ordered_selection(
                example.selection_type, example.selection_context
            )
        except ValueError as exc:  # pragma: no cover - RuleBCExample validates this
            raise BundleAcceptanceError("private binding selection schema is invalid") from exc
        target_digests = list(example.target_action_digests)
        indexed_digests = [candidate_digests[value] for value in chosen_indices]
        if not (
            len(chosen_indices)
            == len(chosen_digests)
            == len(teacher_digests)
            == len(target_digests)
        ):
            raise BundleAcceptanceError("private binding target does not match dataset decision")
        targets_match = (
            indexed_digests == chosen_digests == teacher_digests == target_digests
            if ordered
            else (
                set(indexed_digests)
                == set(chosen_digests)
                == set(teacher_digests)
                == set(target_digests)
            )
        )
        if not targets_match:
            raise BundleAcceptanceError("private binding target does not match dataset decision")


def _read_jsonl_records(path: Path) -> tuple[list[dict[str, object]], None]:
    records: list[dict[str, object]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise BundleAcceptanceError("private binding is unreadable") from exc
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line, parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("non-finite JSON")))
        except (ValueError, json.JSONDecodeError) as exc:
            raise BundleAcceptanceError(f"private binding row {line_number} is malformed") from exc
        if not isinstance(record, dict):
            raise BundleAcceptanceError("private binding row must be an object")
        _require_finite(record)
        records.append(record)
    return records, None


def accept_bundle(bundle_root: str | Path) -> AcceptedBundle:
    root = Path(bundle_root)
    if not root.is_dir() or root.is_symlink():
        raise BundleAcceptanceError("bundle root is unavailable or unsafe")
    manifest = _read_json(root / "dataset_manifest.json")
    dataset_path = _relative_bundle_file(root, manifest.get("dataset_file"), "dataset file")
    dataset_manifest_path = root / "dataset_manifest.json"
    split_manifest_path = root / "split_manifest.json"
    public_summary_path = root / "public_summary.json"
    rows, examples = _read_jsonl(dataset_path)
    split = _read_json(split_manifest_path)
    summary = _read_json(public_summary_path)
    if any(_contains_absolute_path(item) for item in (manifest, split, summary)):
        raise BundleAcceptanceError("public manifest contains an absolute private path")
    if manifest.get("schema_version") != BUNDLE_SCHEMA_VERSION:
        raise BundleAcceptanceError("dataset manifest schema mismatch")
    kind = manifest.get("artifact_purpose")
    eligible = manifest.get("performance_eligible")
    if kind == "SMOKE_ONLY" or kind not in {"ACTUAL_TRAINING", "ACTUAL_TRAINED", "TEST_FIXTURE"}:
        raise BundleAcceptanceError("SMOKE_ONLY or unknown dataset purpose is rejected")
    if type(eligible) is not bool or eligible != (kind in {"ACTUAL_TRAINING", "ACTUAL_TRAINED"}):
        raise BundleAcceptanceError("performance eligibility is inconsistent with bundle purpose")
    if manifest.get("dataset_schema_version") != DATASET_SCHEMA_VERSION:
        raise BundleAcceptanceError("dataset schema mismatch")
    _validate_feature_schema(manifest)
    for field in ("teacher_source", "teacher_version", "teacher_quality", "training_objective"):
        if not isinstance(manifest.get(field), str) or not manifest[field]:
            raise BundleAcceptanceError(f"{field} is missing")
    provenance = manifest.get("trace_provenance_hashes")
    if not isinstance(provenance, list) or not provenance or any(not isinstance(item, str) or not _SHA256.fullmatch(item) for item in provenance):
        raise BundleAcceptanceError("trace provenance is missing")
    if manifest.get("privacy_scan_executed") is not True:
        raise BundleAcceptanceError("privacy scan was not executed")
    _require_zero(manifest.get("privacy_violations"), "privacy_violations")
    dataset_hash = _content_hash(rows)
    if manifest.get("dataset_hash") != dataset_hash:
        raise BundleAcceptanceError("dataset hash mismatch")
    if manifest.get("dataset_file_sha256") != _sha256_bytes(dataset_path.read_bytes()):
        raise BundleAcceptanceError("dataset file hash mismatch")
    manifest_hash = _require_sha256(manifest.get("manifest_hash"), "dataset manifest_hash")
    if manifest_hash != _manifest_hash(manifest):
        raise BundleAcceptanceError("dataset manifest hash mismatch")
    source_ids, candidate_count, chosen_decisions = _validate_rows(examples)
    episode_ids = manifest.get("episode_group_ids")
    if not isinstance(episode_ids, list) or any(not isinstance(item, str) for item in episode_ids) or len(set(episode_ids)) != len(episode_ids) or set(episode_ids) != source_ids:
        raise BundleAcceptanceError("episode groups are missing, duplicated, or inconsistent")
    if manifest.get("episode_count") != len(source_ids) or manifest.get("decision_count") != len(examples) or manifest.get("candidate_count") != candidate_count or manifest.get("chosen_target_decision_count") != chosen_decisions:
        raise BundleAcceptanceError("dataset counts mismatch")
    split_manifest_hash, source_split_hash = _validate_split(split, source_ids=source_ids, dataset_hash=dataset_hash)
    required_summary = {"dataset_hash": dataset_hash, "dataset_manifest_hash": manifest_hash, "split_hash": source_split_hash, "split_manifest_hash": split_manifest_hash}
    if any(summary.get(field) != value for field, value in required_summary.items()):
        raise BundleAcceptanceError("public summary hash mismatch")
    if summary.get("privacy_scan_executed") is not True or summary.get("privacy_violations") != 0:
        raise BundleAcceptanceError("public summary privacy status is invalid")
    if summary.get("artifact_purpose") != kind or summary.get("performance_eligible") is not eligible:
        raise BundleAcceptanceError("public summary purpose or eligibility is inconsistent")
    _validate_private_binding(root, manifest, examples)
    return AcceptedBundle(root, dataset_path, dataset_manifest_path, split_manifest_path, public_summary_path, examples, manifest, split, summary, dataset_hash, manifest_hash, split_manifest_hash, source_split_hash)


def training_commands(bundle: AcceptedBundle, output_root: Path) -> list[list[str]]:
    if not bundle.is_actual_trained:
        raise BundleAcceptanceError("TEST_FIXTURE cannot produce an ACTUAL_TRAINED model")
    base = bundle.dataset_manifest.get("canonical_base_sha")
    if not isinstance(base, str) or not base:
        raise BundleAcceptanceError("canonical_base_sha is missing")
    model = output_root / "trainer-model.json"
    artifact = output_root / "artifact"
    return [
        [sys.executable, str(ROOT / "scripts" / "train_student_v0.py"), "--dataset", str(bundle.dataset_path), "--output", str(model), "--split-manifest", str(bundle.split_manifest_path)],
        [sys.executable, str(ROOT / "scripts" / "evaluate_student_v0.py"), "--dataset", str(bundle.dataset_path), "--model", str(model), "--split-manifest", str(bundle.split_manifest_path), "--partition", "validation"],
        [sys.executable, str(ROOT / "scripts" / "build_student_actual_artifact.py"), "--dataset", str(bundle.dataset_path), "--output-dir", str(artifact), "--canonical-base", base, "--dataset-manifest-hash", bundle.dataset_manifest_hash, "--split-manifest-hash", bundle.split_manifest_hash, "--source-split-hash", bundle.source_split_hash, "--split-manifest", str(bundle.split_manifest_path)],
    ]


def train_bundle(bundle: AcceptedBundle, output_root: str | Path) -> dict[str, object]:
    destination = Path(output_root)
    if destination.exists():
        raise BundleAcceptanceError("training output root already exists")
    commands = training_commands(bundle, destination)
    destination.mkdir(parents=True)
    outputs: list[dict[str, object]] = []
    for command in commands:
        completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
        if completed.returncode != 0:
            raise BundleAcceptanceError("existing Student CLI rejected the accepted bundle")
        try:
            outputs.append(json.loads(completed.stdout))
        except json.JSONDecodeError as exc:
            raise BundleAcceptanceError("existing Student CLI returned malformed output") from exc
    model_path = destination / "artifact" / "student-v0.json"
    manifest_path = destination / "artifact" / "manifest.json"
    _model, model_manifest = load_validated_artifact(model_path, manifest_path)
    expected = {
        "artifact_purpose": "ACTUAL_TRAINED",
        "performance_eligible": True,
        "dataset_hash": bundle.dataset_hash,
        "dataset_manifest_hash": bundle.dataset_manifest_hash,
        "split_manifest_hash": bundle.split_manifest_hash,
        "source_split_hash": bundle.source_split_hash,
    }
    if any(model_manifest.get(field) != value for field, value in expected.items()):
        raise BundleAcceptanceError("model manifest did not inherit accepted provenance")
    result = {**bundle.public_result(), "training_executed": True, "model_hash": model_manifest["model_hash"], "model_size_bytes": model_manifest["model_size_bytes"], "training_outputs": outputs}
    (destination / "acceptance.json").write_text(_canonical_json(result) + "\n", encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-root", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--validate-only", action="store_true")
    mode.add_argument("--train", action="store_true")
    parser.add_argument("--output-root", type=Path)
    args = parser.parse_args(argv)
    try:
        bundle = accept_bundle(args.bundle_root)
        if args.validate_only:
            print(json.dumps(bundle.public_result(), ensure_ascii=False, sort_keys=True))
            return 0
        if args.output_root is None:
            raise BundleAcceptanceError("--train requires --output-root")
        print(json.dumps(train_bundle(bundle, args.output_root), ensure_ascii=False, sort_keys=True))
        return 0
    except (BundleAcceptanceError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"C4 bundle acceptance failed: {type(exc).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
