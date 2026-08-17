"""Consumer-only contracts for C4 actual-training bundle acceptance."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest

import main
from mage_ptcg.student.artifact import build_artifact, feature_schema
from scripts.accept_c4_actual_training_bundle import (
    BUNDLE_SCHEMA_VERSION,
    SPLIT_SCHEMA_VERSION,
    BundleAcceptanceError,
    accept_bundle,
    main as acceptance_main,
    training_commands,
)
from scripts.build_student_actual_artifact import _smoke_examples


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.write_text(_canonical(value) + "\n", encoding="utf-8")


def _manifest_hash(value: dict[str, object]) -> str:
    payload = dict(value)
    payload.pop("manifest_hash", None)
    return _hash(payload)


def _write_bundle(root: Path, *, kind: str = "TEST_FIXTURE") -> Path:
    root.mkdir()
    examples = _smoke_examples()
    rows = [item.to_dict() for item in examples]
    dataset = root / "rule-bc-v1.jsonl"
    dataset.write_text("".join(_canonical(row) + "\n" for row in rows), encoding="utf-8")
    dataset_hash = _hash(rows)
    source_ids = sorted({item.source_id for item in examples})
    assignments = {source_id: ("validation" if index == 0 else "train") for index, source_id in enumerate(source_ids)}
    split_hash = _hash({"assignments": dict(sorted(assignments.items())), "dataset_hash": dataset_hash})
    split: dict[str, object] = {
        "schema_version": SPLIT_SCHEMA_VERSION,
        "dataset_hash": dataset_hash,
        "split_method": "episode_group_hash_v0",
        "split_seed": 0,
        "assignments": assignments,
        "train_episode_count": len(source_ids) - 1,
        "validation_episode_count": 1,
        "split_overlap_count": 0,
        "duplicate_episode_count": 0,
        "duplicate_decision_count": 0,
        "split_hash": split_hash,
    }
    split["manifest_hash"] = _manifest_hash(split)
    _write_json(root / "split_manifest.json", split)
    schema = feature_schema()
    manifest: dict[str, object] = {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "artifact_purpose": kind,
        "performance_eligible": kind == "ACTUAL_TRAINED",
        "dataset_schema_version": "rule-bc-v1",
        "dataset_file": "rule-bc-v1.jsonl",
        "dataset_hash": dataset_hash,
        "dataset_file_sha256": _file_hash(dataset),
        "episode_group_ids": source_ids,
        "episode_count": len(source_ids),
        "decision_count": len(rows),
        "candidate_count": sum(len(item["legal_actions"]) for item in rows),
        "chosen_target_decision_count": len(rows),
        "teacher_source": "TEST_FIXTURE_RULE_V0",
        "teacher_version": "test-v0",
        "teacher_quality": "TEST_ONLY_NOT_PERFORMANCE_EVIDENCE",
        "training_objective": "RULE_IMITATION",
        "trace_provenance_hashes": ["a" * 64],
        "privacy_scan_executed": True,
        "privacy_violations": 0,
        "canonical_base_sha": "b" * 40,
        **schema,
    }
    manifest["manifest_hash"] = _manifest_hash(manifest)
    _write_json(root / "dataset_manifest.json", manifest)
    summary = {
        "artifact_purpose": kind,
        "performance_eligible": kind == "ACTUAL_TRAINED",
        "dataset_hash": dataset_hash,
        "dataset_manifest_hash": manifest["manifest_hash"],
        "split_hash": split["split_hash"],
        "split_manifest_hash": split["manifest_hash"],
        "privacy_scan_executed": True,
        "privacy_violations": 0,
    }
    _write_json(root / "public_summary.json", summary)
    return root


def _refresh_bundle(root: Path) -> None:
    dataset = root / "rule-bc-v1.jsonl"
    rows = [json.loads(line) for line in dataset.read_text(encoding="utf-8").splitlines() if line]
    dataset_hash = _hash(rows)
    manifest = json.loads((root / "dataset_manifest.json").read_text(encoding="utf-8"))
    manifest["dataset_hash"] = dataset_hash
    manifest["dataset_file_sha256"] = _file_hash(dataset)
    manifest["manifest_hash"] = _manifest_hash(manifest)
    _write_json(root / "dataset_manifest.json", manifest)
    split = json.loads((root / "split_manifest.json").read_text(encoding="utf-8"))
    split["dataset_hash"] = dataset_hash
    split["split_hash"] = _hash({"assignments": dict(sorted(split["assignments"].items())), "dataset_hash": dataset_hash})
    split["manifest_hash"] = _manifest_hash(split)
    _write_json(root / "split_manifest.json", split)
    _write_json(root / "public_summary.json", {
        "artifact_purpose": manifest["artifact_purpose"],
        "performance_eligible": manifest["performance_eligible"],
        "dataset_hash": dataset_hash,
        "dataset_manifest_hash": manifest["manifest_hash"],
        "split_hash": split["split_hash"],
        "split_manifest_hash": split["manifest_hash"],
        "privacy_scan_executed": True,
        "privacy_violations": 0,
    })


def test_valid_test_fixture_bundle_is_accepted_but_not_trainable(tmp_path: Path) -> None:
    bundle = accept_bundle(_write_bundle(tmp_path / "bundle"))
    assert bundle.public_result()["accepted"] is True
    assert bundle.public_result()["performance_eligible"] is False
    with pytest.raises(BundleAcceptanceError, match="TEST_FIXTURE"):
        training_commands(bundle, tmp_path / "output")


def test_validate_only_cli_accepts_test_fixture_without_training(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _write_bundle(tmp_path / "bundle")
    assert acceptance_main(["--bundle-root", str(root), "--validate-only"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["accepted"] is True
    assert result["performance_eligible"] is False


def test_missing_dataset_is_rejected(tmp_path: Path) -> None:
    root = _write_bundle(tmp_path / "bundle")
    (root / "rule-bc-v1.jsonl").unlink()
    with pytest.raises(BundleAcceptanceError, match="dataset"):
        accept_bundle(root)


def test_dataset_hash_mismatch_is_rejected(tmp_path: Path) -> None:
    root = _write_bundle(tmp_path / "bundle")
    manifest = json.loads((root / "dataset_manifest.json").read_text(encoding="utf-8"))
    manifest["dataset_hash"] = "0" * 64
    manifest["manifest_hash"] = _manifest_hash(manifest)
    _write_json(root / "dataset_manifest.json", manifest)
    with pytest.raises(BundleAcceptanceError, match="dataset hash"):
        accept_bundle(root)


def test_split_overlap_is_rejected(tmp_path: Path) -> None:
    root = _write_bundle(tmp_path / "bundle")
    split = json.loads((root / "split_manifest.json").read_text(encoding="utf-8"))
    split["split_overlap_count"] = 1
    split["manifest_hash"] = _manifest_hash(split)
    _write_json(root / "split_manifest.json", split)
    summary = json.loads((root / "public_summary.json").read_text(encoding="utf-8"))
    summary["split_manifest_hash"] = split["manifest_hash"]
    summary["split_hash"] = split["split_hash"]
    _write_json(root / "public_summary.json", summary)
    with pytest.raises(BundleAcceptanceError, match="overlap"):
        accept_bundle(root)


def test_invalid_chosen_target_is_rejected(tmp_path: Path) -> None:
    root = _write_bundle(tmp_path / "bundle")
    dataset = root / "rule-bc-v1.jsonl"
    rows = [json.loads(line) for line in dataset.read_text(encoding="utf-8").splitlines()]
    rows[0]["target_action_digests"] = ["not-a-legal-action"]
    dataset.write_text("".join(_canonical(row) + "\n" for row in rows), encoding="utf-8")
    _refresh_bundle(root)
    with pytest.raises(BundleAcceptanceError, match="invalid"):
        accept_bundle(root)


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("feature_dimension", 95, "feature schema"),
        ("privacy_scan_executed", False, "privacy scan"),
        ("privacy_violations", 1, "privacy_violations"),
        ("teacher_source", "", "teacher_source"),
        ("trace_provenance_hashes", [], "trace provenance"),
        ("artifact_purpose", "SMOKE_ONLY", "SMOKE_ONLY"),
        ("performance_eligible", True, "performance eligibility"),
    ],
)
def test_manifest_fail_closed_cases(tmp_path: Path, field: str, value: object, error: str) -> None:
    root = _write_bundle(tmp_path / "bundle")
    manifest = json.loads((root / "dataset_manifest.json").read_text(encoding="utf-8"))
    manifest[field] = value
    manifest["manifest_hash"] = _manifest_hash(manifest)
    _write_json(root / "dataset_manifest.json", manifest)
    summary = json.loads((root / "public_summary.json").read_text(encoding="utf-8"))
    summary["dataset_manifest_hash"] = manifest["manifest_hash"]
    _write_json(root / "public_summary.json", summary)
    with pytest.raises(BundleAcceptanceError, match=error):
        accept_bundle(root)


def test_non_finite_dataset_and_public_absolute_path_are_rejected(tmp_path: Path) -> None:
    root = _write_bundle(tmp_path / "nonfinite")
    dataset = root / "rule-bc-v1.jsonl"
    dataset.write_text(dataset.read_text(encoding="utf-8").replace('"turn":2', '"turn":NaN', 1), encoding="utf-8")
    with pytest.raises(BundleAcceptanceError, match="invalid"):
        accept_bundle(root)
    root = _write_bundle(tmp_path / "path")
    summary = json.loads((root / "public_summary.json").read_text(encoding="utf-8"))
    summary["unsafe_path"] = "/private/trace.jsonl"
    _write_json(root / "public_summary.json", summary)
    with pytest.raises(BundleAcceptanceError, match="absolute"):
        accept_bundle(root)


def test_existing_cli_arguments_and_model_provenance_are_fixed(tmp_path: Path) -> None:
    fixture = accept_bundle(_write_bundle(tmp_path / "bundle"))
    actual = replace(fixture, dataset_manifest={**fixture.dataset_manifest, "artifact_purpose": "ACTUAL_TRAINED", "performance_eligible": True})
    commands = training_commands(actual, tmp_path / "output")
    assert [Path(command[1]).name for command in commands] == ["train_student_v0.py", "evaluate_student_v0.py", "build_student_actual_artifact.py"]
    assert "--dataset-manifest-hash" in commands[-1]
    assert "--split-manifest-hash" in commands[-1]
    artifact = tmp_path / "artifact"
    manifest = build_artifact(
        examples=_smoke_examples(), output_dir=artifact, canonical_base_sha="a" * 40,
        work_commit_sha="b" * 40, dataset_source_type="TEST_FIXTURE", artifact_purpose="SMOKE_ONLY", epochs=5,
        dataset_manifest_hash="c" * 64, split_manifest_hash="d" * 64, source_split_hash="e" * 64,
    )
    assert manifest["dataset_manifest_hash"] == "c" * 64
    assert manifest["split_manifest_hash"] == "d" * 64
    assert manifest["source_split_hash"] == "e" * 64
    assert manifest["performance_eligible"] is False


def test_rule_champion_default_is_unchanged() -> None:
    assert main._DEFAULT_AGENT.__name__ == "rule_legal_agent"
