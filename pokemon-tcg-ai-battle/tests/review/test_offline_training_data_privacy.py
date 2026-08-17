"""Adversarial dataset privacy and leakage checks for Offline Training v1.

Includes the mutation-killer for the row-level forbidden-observation-key check
(the production suite's privacy test rejects its record via the selection-bounds
check, not the privacy check, so disabling `_contains_forbidden_key` survives
the whole production suite -- REV-M1).
"""

from __future__ import annotations

import gzip
import json

import pytest

torch = pytest.importorskip("torch")

from mage_ptcg.offline_training.dataset import (
    OfflineDatasetError,
    build_dataset,
    iter_decisions,
    load_manifest,
)
from mage_ptcg.student.dataset import DatasetValidationError, load_dataset


def _valid_record(review_collected) -> dict:
    line = review_collected.read_text(encoding="utf-8").splitlines()[0]
    return json.loads(line)


def test_forbidden_key_rejected_on_structurally_valid_record(review_collected, tmp_path):
    """Kills REV-M1: inject a forbidden observation key into an otherwise fully
    valid record so ONLY the privacy check can reject it."""
    record = _valid_record(review_collected)
    record["public_state"] = dict(record["public_state"])
    record["public_state"]["logs"] = ["engine internals"]
    bad = tmp_path / "bad.jsonl"
    bad.write_text(json.dumps(record) + "\n", encoding="utf-8")
    with pytest.raises(DatasetValidationError):
        load_dataset(bad)


def test_forbidden_key_in_own_private_state_rejected(review_collected, tmp_path):
    record = _valid_record(review_collected)
    record["own_private_state"] = dict(record["own_private_state"])
    record["own_private_state"]["search_begin_input"] = {"opaque": True}
    bad = tmp_path / "bad.jsonl"
    bad.write_text(json.dumps(record) + "\n", encoding="utf-8")
    with pytest.raises(DatasetValidationError):
        load_dataset(bad)


def test_nested_forbidden_key_rejected(review_collected, tmp_path):
    record = _valid_record(review_collected)
    record["public_state"] = dict(record["public_state"])
    record["public_state"]["wrapper"] = [{"remainingOverageTime": 3}]
    bad = tmp_path / "bad.jsonl"
    bad.write_text(json.dumps(record) + "\n", encoding="utf-8")
    with pytest.raises(DatasetValidationError):
        load_dataset(bad)


def test_source_ids_are_redacted_hashes(review_collected):
    for line in review_collected.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        assert record["source_id"].startswith("sha256:"), "source_id must be a redacted hash"
        assert record["metadata"]["source_identifier"].startswith("sha256:")


def test_dataset_manifest_carries_no_raw_identifiers(review_dataset_dir):
    from mage_ptcg.dataops.collector import scan_public_artifact

    manifest = load_manifest(review_dataset_dir)
    scan = scan_public_artifact(manifest)
    assert scan["privacy_violations"] == 0, scan["privacy_violation_categories"]
    for source_id in manifest["split_assignment"]:
        assert source_id.startswith("sha256:")


def _build(source_jsonl, out):
    return build_dataset(
        source_jsonl=source_jsonl, output_dir=out, shard_size=8, split_seed=99,
        train_fraction=0.5, validation_fraction=0.25, test_fraction=0.25,
        teacher_id="t", trainer_id="tr", source_collection_hash="NONE",
    )


def test_duplicate_identical_decision_deduplicated(review_collected, tmp_path):
    """The same (episode, decision_index, example_id) row twice must collapse to
    one record: the manifest must be unchanged relative to the baseline build.
    (Invariant across the pre/post split-leakage-quarantine versions.)"""
    lines = review_collected.read_text(encoding="utf-8").splitlines()
    baseline = _build(review_collected, tmp_path / "base")
    doubled = tmp_path / "doubled.jsonl"
    doubled.write_text("\n".join(lines + [lines[0]]) + "\n", encoding="utf-8")
    manifest = _build(doubled, tmp_path / "out")
    assert manifest["record_count"] == baseline["record_count"]
    assert manifest["duplicate_conflict_count"] == baseline["duplicate_conflict_count"]
    assert manifest["dataset_hash"] == baseline["dataset_hash"]


def test_conflicting_duplicate_decision_quarantined(review_collected, tmp_path):
    """Two different rows claiming the same (episode, decision_index) identity
    must be quarantined, not silently resolved."""
    lines = review_collected.read_text(encoding="utf-8").splitlines()
    baseline = _build(review_collected, tmp_path / "base")
    already = {tuple(item) for item in baseline["quarantined_identities"]}
    # pick a record whose identity survives the baseline build
    target = None
    for line in lines:
        record = json.loads(line)
        identity = (record["source_id"], record["metadata"]["decision_index"])
        if identity not in already:
            target = record
            break
    assert target is not None, "fixture produced no surviving record to conflict"
    conflict = dict(target)
    conflict["example_id"] = "0" * 64  # different content, same identity
    mixed = tmp_path / "conflict.jsonl"
    mixed.write_text("\n".join(lines + [json.dumps(conflict)]) + "\n", encoding="utf-8")
    manifest = _build(mixed, tmp_path / "out")
    identity = [target["source_id"], target["metadata"]["decision_index"]]
    assert identity in manifest["quarantined_identities"]
    assert manifest["duplicate_conflict_count"] >= baseline["duplicate_conflict_count"] + 1
    # the quarantined decision must appear in no split
    for split in ("train", "validation", "test"):
        for decision in iter_decisions(tmp_path / "out", split):
            assert not (
                decision.source_id == target["source_id"]
                and decision.example_id in (target["example_id"], conflict["example_id"])
            )


def test_train_only_normalization_excludes_validation_and_test(review_dataset_dir):
    """Recompute normalization from the train split alone and compare."""
    manifest = load_manifest(review_dataset_dir)
    rows = []
    for decision in iter_decisions(review_dataset_dir, "train"):
        rows.extend([list(r) for r in decision.candidate_features])
    dim = manifest["feature_dimension"]
    count = len(rows)
    means = [sum(r[i] for r in rows) / count for i in range(dim)]
    norm = manifest["normalization"]
    assert norm["count"] == count
    for got, want in zip(norm["mean"], means):
        assert abs(got - want) < 1e-9


def test_shard_bytes_are_deterministic(review_collected, tmp_path):
    """Rebuilding the dataset from the same JSONL must give identical shard
    bytes and dataset hash (gzip mtime pinned to 0)."""
    outs = []
    for name in ("a", "b"):
        out = tmp_path / name
        manifest = build_dataset(
            source_jsonl=review_collected, output_dir=out, shard_size=8, split_seed=99,
            train_fraction=0.5, validation_fraction=0.25, test_fraction=0.25,
            teacher_id="t", trainer_id="tr", source_collection_hash="NONE",
        )
        outs.append((out, manifest))
    (dir_a, man_a), (dir_b, man_b) = outs
    assert man_a["dataset_hash"] == man_b["dataset_hash"]
    for shard in man_a["shards"]:
        assert (dir_a / shard["name"]).read_bytes() == (dir_b / shard["name"]).read_bytes()


def test_build_dataset_refuses_nonempty_output(review_collected, tmp_path):
    out = tmp_path / "occupied"
    out.mkdir()
    (out / "junk").write_text("x")
    with pytest.raises(OfflineDatasetError):
        build_dataset(
            source_jsonl=review_collected, output_dir=out, shard_size=8, split_seed=99,
            train_fraction=0.5, validation_fraction=0.25, test_fraction=0.25,
            teacher_id="t", trainer_id="tr", source_collection_hash="NONE",
        )


def test_shard_payloads_stay_in_private_gzip(review_dataset_dir):
    """Candidate ActionKey payloads live only inside the (git-ignored) shards;
    the manifest itself must not embed candidate payloads or digests."""
    manifest = load_manifest(review_dataset_dir)
    text = json.dumps(manifest)
    with gzip.open(review_dataset_dir / manifest["shards"][0]["name"], "rt", encoding="utf-8") as handle:
        record = json.loads(handle.readline())
    a_digest = record["legal_actions"][0]["digest"]
    assert a_digest not in text, "manifest leaks candidate digests"
