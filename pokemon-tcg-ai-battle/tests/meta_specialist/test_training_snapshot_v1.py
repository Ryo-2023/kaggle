"""L1 training snapshot: sealed-envelope intake, grouped splits, byte-identical publication."""

from __future__ import annotations

import copy
import os
from pathlib import Path
import stat

import pytest

from mage_ptcg.meta_specialist.local_dataset_v2 import (
    LocalDatasetV2Error,
    assign_grouped_splits_from_keys_v2,
    assign_grouped_splits_v2,
    atomic_write_local_dataset_v2,
    build_local_dataset_manifest_v2,
    canonical_json_bytes_v2,
    parse_canonical_json_bytes_v2,
)
from mage_ptcg.meta_specialist.training_snapshot_v1 import (
    DEFAULT_SPLIT_NAMES_V1,
    DEFAULT_SPLIT_WEIGHTS_V1,
    TRAINING_SNAPSHOT_SCHEMA_V1,
    TrainingSnapshotV1Error,
    atomic_write_training_snapshot_v1,
    build_training_snapshot_v1,
    read_training_snapshot_v1,
    snapshot_examples_for_split_v1,
    validate_training_snapshot_v1,
)

from tests.meta_specialist.test_training_example_envelope_v2 import (
    _qualified_dataset,
    _qualified_two_record_dataset,
)


QUALIFIED_AT = "2026-08-02T00:00:00Z"


def _build(tmp_path, *, two: bool = False, **overrides):
    factory = _qualified_two_record_dataset if two else _qualified_dataset
    path, records, manifest, permission, trusted, vocabulary = factory(tmp_path)
    kwargs = {
        "manifest": manifest,
        "vocabulary": vocabulary,
        "trusted_permissions": trusted,
        "qualification_time_utc": QUALIFIED_AT,
    }
    kwargs.update(overrides)
    snapshot = build_training_snapshot_v1(path, **kwargs)
    return snapshot, path, records, manifest, permission, trusted, vocabulary


def test_snapshot_publishes_sealed_envelope_content_with_verified_identity(tmp_path) -> None:
    snapshot, _path, record, manifest, permission, _trusted, vocabulary = _build(tmp_path)

    assert snapshot["schema_version"] == TRAINING_SNAPSHOT_SCHEMA_V1
    assert snapshot["manifest_id"] == manifest["manifest_id"]
    assert snapshot["manifest_content_hash"] == manifest["content_hash"]
    assert snapshot["vocabulary_source_sha256"] == vocabulary.source_sha256
    assert snapshot["vocabulary_environment_version"] == vocabulary.environment_version
    assert snapshot["qualification_time_utc"] == QUALIFIED_AT
    assert snapshot["split_names"] == list(DEFAULT_SPLIT_NAMES_V1)
    assert snapshot["permissions"] == [{
        "permission_manifest_id": permission["permission_manifest_id"],
        "permission_content_hash": snapshot["permissions"][0]["permission_content_hash"],
        "permission_trusted_bytes_sha256": snapshot["permissions"][0][
            "permission_trusted_bytes_sha256"
        ],
    }]

    examples = snapshot["examples"]
    assert len(examples) == 1
    example = examples[0]
    assert example["record_id"] == record["record_id"]
    assert example["episode_id_hash"] == record["episode_id_hash"]
    assert example["record_content_hash"] == record["content_hash"]
    assert example["value_target"] == 0.25
    assert example["loss_rows"] and example["model_input"]["candidate_rows"]
    assert sum(snapshot["split_counts"].values()) == 1


def test_snapshot_carries_no_private_binding_fields(tmp_path) -> None:
    snapshot, *_rest = _build(tmp_path)

    def keys(value: object) -> set[str]:
        if type(value) is dict:
            return set(value) | set().union(*(keys(item) for item in value.values()), set())
        if type(value) is list:
            return set().union(*(keys(item) for item in value), set())
        return set()

    leaked = keys(snapshot) & {
        "local_action_id", "action_key_digest", "action_key_payload", "actor_binding",
        "serial", "index", "record", "game_id", "path",
    }
    assert leaked == set()


def test_snapshot_masses_sum_to_one_and_quality_weight_is_carried_once(tmp_path) -> None:
    import math

    snapshot, *_rest = _build(tmp_path)
    example = snapshot["examples"][0]
    assert example["example_quality_weight"] == 1.0
    for row in example["loss_rows"]:
        masses = [token["mass"] for token in row["token_masses"]]
        assert math.isclose(math.fsum(masses), 1.0, rel_tol=0.0, abs_tol=1e-12)
        assert all(math.isfinite(mass) for mass in masses)
        # The quality weight lives once on the example, never duplicated per row.
        assert "quality_weight" not in row
        assert 0.0 < row["reach_mass"] <= 1.0


def test_grouped_split_matches_the_raw_record_planner_and_never_straddles(tmp_path) -> None:
    snapshot, _path, records, *_rest = _build(tmp_path, two=True)

    # The planner must be given the snapshot's own weights.  Comparing against
    # the uniform default would agree by coincidence on a fixture this small and
    # would stop detecting a real divergence.
    weights = tuple(snapshot["split_weights"])
    assert weights == DEFAULT_SPLIT_WEIGHTS_V1
    expected = assign_grouped_splits_v2(
        tuple(records), split_names=DEFAULT_SPLIT_NAMES_V1, split_weights=weights
    )
    actual = {item["record_id"]: item["split"] for item in snapshot["examples"]}
    assert actual == {rid: expected[rid] for rid in actual}

    keys = tuple(
        (item["record_id"], item["episode_id_hash"], item["near_duplicate_id"])
        for item in snapshot["examples"]
    )
    assert actual == assign_grouped_splits_from_keys_v2(
        keys, split_names=DEFAULT_SPLIT_NAMES_V1, split_weights=weights
    )

    by_component: dict[str, set[str]] = {}
    for item in snapshot["examples"]:
        for group in (item["episode_id_hash"], item["near_duplicate_id"]):
            by_component.setdefault(group, set()).add(item["split"])
    assert all(len(splits) == 1 for splits in by_component.values())


def test_validation_rejects_a_component_that_straddles_two_splits(tmp_path) -> None:
    snapshot, *_rest = _build(tmp_path, two=True)
    tampered = copy.deepcopy(snapshot)
    shared = tampered["examples"][0]["near_duplicate_id"]
    tampered["examples"][1]["near_duplicate_id"] = shared
    other = next(
        name for name in tampered["split_names"] if name != tampered["examples"][0]["split"]
    )
    tampered["examples"][0]["split"] = tampered["examples"][0]["split"]
    tampered["examples"][1]["split"] = other
    tampered["split_counts"] = {
        name: sum(1 for item in tampered["examples"] if item["split"] == name)
        for name in tampered["split_names"]
    }
    if tampered["examples"][0]["split"] == tampered["examples"][1]["split"]:
        pytest.skip("fixture components already share one split")
    with pytest.raises(TrainingSnapshotV1Error, match="leaks one grouping component"):
        validate_training_snapshot_v1(tampered)


def test_snapshot_is_deterministic_and_publication_is_byte_identical(tmp_path) -> None:
    snapshot, path, _records, manifest, _permission, trusted, vocabulary = _build(tmp_path)
    again = build_training_snapshot_v1(
        path, manifest=manifest, vocabulary=vocabulary, trusted_permissions=trusted,
        qualification_time_utc=QUALIFIED_AT,
    )
    assert canonical_json_bytes_v2(again) == canonical_json_bytes_v2(snapshot)

    first = tmp_path / "out" / "snapshot.json"
    second = tmp_path / "out" / "snapshot-again.json"
    atomic_write_training_snapshot_v1(first, snapshot)
    atomic_write_training_snapshot_v1(second, again)
    assert first.read_bytes() == second.read_bytes()
    assert read_training_snapshot_v1(first) == snapshot
    assert list(first.parent.glob(".snapshot.json.tmp.*")) == []


def test_published_snapshot_rejects_noncanonical_and_tampered_bytes(tmp_path) -> None:
    snapshot, *_rest = _build(tmp_path)
    published = tmp_path / "snapshot.json"
    atomic_write_training_snapshot_v1(published, snapshot)

    payload = parse_canonical_json_bytes_v2(published.read_bytes())
    payload["examples"][0]["value_target"] = 0.75
    published.write_bytes(canonical_json_bytes_v2(payload))
    with pytest.raises(TrainingSnapshotV1Error, match="snapshot_id does not verify"):
        read_training_snapshot_v1(published)

    published.write_bytes(b"  " + canonical_json_bytes_v2(snapshot))
    with pytest.raises(Exception):
        read_training_snapshot_v1(published)


def test_snapshot_id_and_content_hash_bind_every_identity_field(tmp_path) -> None:
    snapshot, *_rest = _build(tmp_path)
    for field, value in (
        ("dataset_snapshot_sha256", "b" * 64),
        ("manifest_id", "c" * 64),
        ("feature_schema_hash", "d" * 64),
        ("vocabulary_source_sha256", "e" * 64),
        ("qualification_time_utc", "2026-08-02T12:00:00Z"),
    ):
        tampered = copy.deepcopy(snapshot)
        tampered[field] = value
        with pytest.raises(TrainingSnapshotV1Error, match="does not verify"):
            validate_training_snapshot_v1(tampered)


def test_snapshot_rejects_unknown_split_duplicate_and_unsorted_examples(tmp_path) -> None:
    snapshot, *_rest = _build(tmp_path, two=True)

    unknown = copy.deepcopy(snapshot)
    unknown["examples"][0]["split"] = "holdout"
    with pytest.raises(TrainingSnapshotV1Error, match="unknown split"):
        validate_training_snapshot_v1(unknown)

    duplicated = copy.deepcopy(snapshot)
    duplicated["examples"][1]["record_id"] = duplicated["examples"][0]["record_id"]
    with pytest.raises(TrainingSnapshotV1Error, match="record_id must be unique|sorted by record_id"):
        validate_training_snapshot_v1(duplicated)

    unsorted = copy.deepcopy(snapshot)
    unsorted["examples"].reverse()
    with pytest.raises(TrainingSnapshotV1Error, match="sorted by record_id"):
        validate_training_snapshot_v1(unsorted)


def test_snapshot_rejects_mismatched_split_counts_and_open_field_sets(tmp_path) -> None:
    snapshot, *_rest = _build(tmp_path)

    miscounted = copy.deepcopy(snapshot)
    miscounted["split_counts"] = {name: 99 for name in miscounted["split_names"]}
    with pytest.raises(TrainingSnapshotV1Error, match="split_counts do not match"):
        validate_training_snapshot_v1(miscounted)

    extra = copy.deepcopy(snapshot)
    extra["unexpected"] = 1
    with pytest.raises(TrainingSnapshotV1Error, match="wrong closed field set"):
        validate_training_snapshot_v1(extra)

    open_example = copy.deepcopy(snapshot)
    open_example["examples"][0]["serial"] = 7
    with pytest.raises(TrainingSnapshotV1Error, match="wrong closed field set"):
        validate_training_snapshot_v1(open_example)


@pytest.mark.parametrize("when", ["2026-07-31T23:59:59Z", "2026-08-03T00:00:01Z"])
def test_snapshot_fails_closed_outside_the_permission_validity_window(tmp_path, when: str) -> None:
    # Expiry and not-yet-valid both stop the build outright rather than quietly
    # publishing a smaller snapshot.
    with pytest.raises(LocalDatasetV2Error, match="not live at the requested qualification time"):
        _build(tmp_path, qualification_time_utc=when)


def test_snapshot_rejects_source_growth_and_is_content_addressed(tmp_path) -> None:
    _snapshot, path, record, manifest, _permission, trusted, vocabulary = _build(tmp_path)

    grown = copy.deepcopy(record)
    grown["decision_index"] = 9
    with open(path, "ab") as handle:
        handle.write(canonical_json_bytes_v2(grown) + b"\n")
    with pytest.raises(Exception):
        build_training_snapshot_v1(
            path, manifest=manifest, vocabulary=vocabulary, trusted_permissions=trusted,
            qualification_time_utc=QUALIFIED_AT,
        )

    # The dataset file holds records only, so identity is content-addressed: a
    # byte-identical rewrite at another path is the same snapshot, and any real
    # content change is a different snapshot.
    same_content = path.parent / "same-content.jsonl"
    atomic_write_local_dataset_v2(same_content, records=(record,), manifest=manifest)
    identical = build_training_snapshot_v1(
        same_content, manifest=manifest, vocabulary=vocabulary, trusted_permissions=trusted,
        qualification_time_utc=QUALIFIED_AT,
    )
    assert identical["dataset_snapshot_sha256"] == _snapshot["dataset_snapshot_sha256"]
    assert identical["snapshot_id"] == _snapshot["snapshot_id"]


def test_a_different_record_set_is_a_different_snapshot_identity(tmp_path) -> None:
    one, *_rest = _build(tmp_path / "one")
    two, *_more = _build(tmp_path / "two", two=True)

    assert one["dataset_snapshot_sha256"] != two["dataset_snapshot_sha256"]
    assert one["manifest_id"] != two["manifest_id"]
    assert one["snapshot_id"] != two["snapshot_id"]
    assert len(one["examples"]) == 1 and len(two["examples"]) == 2


def test_snapshot_requires_a_sealed_vocabulary_and_two_split_names(tmp_path) -> None:
    _snapshot, path, _record, manifest, _permission, trusted, vocabulary = _build(tmp_path)

    with pytest.raises(TrainingSnapshotV1Error, match="sealed CardVocabularyV1"):
        build_training_snapshot_v1(
            path, manifest=manifest, vocabulary=object(), trusted_permissions=trusted,
            qualification_time_utc=QUALIFIED_AT,
        )
    with pytest.raises(TrainingSnapshotV1Error, match="at least two names"):
        build_training_snapshot_v1(
            path, manifest=manifest, vocabulary=vocabulary, trusted_permissions=trusted,
            qualification_time_utc=QUALIFIED_AT, split_names=("train",),
        )


def test_split_accessor_returns_detached_copies_in_record_order(tmp_path) -> None:
    snapshot, *_rest = _build(tmp_path, two=True)
    seen: list[str] = []
    for name in snapshot["split_names"]:
        rows = snapshot_examples_for_split_v1(snapshot, name)
        assert [row["record_id"] for row in rows] == sorted(row["record_id"] for row in rows)
        for row in rows:
            row["value_target"] = 99.0  # detached: must not affect the snapshot
        seen.extend(row["record_id"] for row in rows)
    assert sorted(seen) == sorted(item["record_id"] for item in snapshot["examples"])
    assert all(item["value_target"] != 99.0 for item in snapshot["examples"])

    with pytest.raises(TrainingSnapshotV1Error, match="unknown split"):
        snapshot_examples_for_split_v1(snapshot, "nope")


def test_publication_preserves_an_existing_directory_and_cleans_temporaries(tmp_path) -> None:
    snapshot, *_rest = _build(tmp_path)
    target = tmp_path / "published" / "snapshot.json"
    target.parent.mkdir(parents=True)
    target.mkdir()
    with pytest.raises(OSError):
        atomic_write_training_snapshot_v1(target, snapshot)
    assert stat.S_ISDIR(target.lstat().st_mode)
    assert list(target.parent.glob(".snapshot.json.tmp.*")) == []


def test_publication_is_relative_path_safe_after_chdir(tmp_path, monkeypatch) -> None:
    snapshot, *_rest = _build(tmp_path)
    workdir = tmp_path / "work"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    result = atomic_write_training_snapshot_v1(Path("snapshot.json"), snapshot)
    assert result == workdir / "snapshot.json"
    assert read_training_snapshot_v1(result) == snapshot
    assert os.getcwd() == str(workdir)
