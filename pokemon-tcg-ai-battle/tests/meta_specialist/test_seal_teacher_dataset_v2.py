"""Collector v2 sealing preflight and snapshot provenance closure."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.local_dataset_v2 import (
    canonical_json_bytes_v2,
    make_source_permission_manifest_v1,
)
from mage_ptcg.meta_specialist.training_snapshot_v1 import (
    SHARD_INDEX_SCHEMA_V1,
    TRAINING_SHARD_SCHEMA_V1,
    _snapshot_content_hash,
    _snapshot_identity,
    atomic_write_training_snapshot_v1,
    read_training_snapshot_v1,
)
from scripts.seal_teacher_dataset import (
    CollectionSealPreflightV2Error,
    _bind_collection_provenance_to_sharded_output_v2,
    _bind_collection_provenance_to_snapshot_v2,
    _preflight_collection_v2,
)


_ENVELOPE_FIXTURE_SPEC = importlib.util.spec_from_file_location(
    "_seal_v2_envelope_fixture",
    Path(__file__).with_name("test_training_example_envelope_v2.py"),
)
assert _ENVELOPE_FIXTURE_SPEC is not None and _ENVELOPE_FIXTURE_SPEC.loader is not None
_ENVELOPE_FIXTURE_MODULE = importlib.util.module_from_spec(_ENVELOPE_FIXTURE_SPEC)
_ENVELOPE_FIXTURE_SPEC.loader.exec_module(_ENVELOPE_FIXTURE_MODULE)
_qualified_dataset = _ENVELOPE_FIXTURE_MODULE._qualified_dataset


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes_v2(value) + b"\n")


def _make_collection(tmp_path: Path) -> tuple[Path, dict]:
    run = tmp_path / "run"
    records = run / "records"
    records.mkdir(parents=True)

    subject_deck = tmp_path / "subject.csv"
    subject_deck.write_text("1\n" * 60, encoding="utf-8")
    pool = tmp_path / "pool"
    teacher_dir = pool / "teacher-x"
    teacher_dir.mkdir(parents=True)
    teacher_deck = teacher_dir / "deck.csv"
    teacher_deck.write_text("2\n" * 60, encoding="utf-8")
    teacher_policy = teacher_dir / "main.py"
    teacher_policy.write_text("def agent(obs): return [0]\n", encoding="utf-8")
    pool_manifest = [{
        "id": "teacher-x",
        "canonical_deck_hash": "a" * 64,
        "policy_hash": _sha(teacher_policy),
        "source": "public",
        "usage_boundary": "local_eval_only",
    }]
    _write_json(pool / "pool_manifest.json", pool_manifest)

    source_kind = "pooled_external_submission_agent"
    permission = make_source_permission_manifest_v1(
        artifact_sha256=_sha(teacher_policy),
        source_kind=source_kind,
        allowed_usages=("training-local",),
        revision="fixture-v2",
        issuer="docs/decision.md",
        valid_from_utc=None,
        expires_at_utc=None,
    )
    contract = {
        "schema_version": "specialist-teacher-collection-contract-v2",
        "run_name": "fixture-v2",
        "archetype_id": "archaludon",
        "subject_deck_csv_path": str(subject_deck.resolve()),
        "subject_deck_file_sha256": _sha(subject_deck),
        "teacher": {
            "opponent_id": "teacher-x",
            "policy_sha256": _sha(teacher_policy),
            "deck_file_sha256": _sha(teacher_deck),
            "canonical_deck_hash": "a" * 64,
            "source": "public",
            "usage_boundary": "local_eval_only",
        },
        "teacher_source_kind": source_kind,
        "opponent_ids": ["opponent-a"],
        "opponents": [{"opponent_id": "opponent-a"}],
        "games_requested": 1,
        "base_seed": 700,
        "max_steps": 2000,
        "source_commit": "b" * 40,
        "decision_ref": "docs/decision.md",
        "permission_manifest_id": permission["permission_manifest_id"],
        "permission_content_hash": permission["content_hash"],
        "permission_trusted_bytes_sha256": hashlib.sha256(
            canonical_json_bytes_v2(permission)
        ).hexdigest(),
        "allowed_usages": ["training-local"],
        "pool_root": str(pool.resolve()),
        "pool_manifest_sha256": _sha(pool / "pool_manifest.json"),
        "engine_entry_point": "scripts.test_sim:run_match",
        "engine_source_sha256": "c" * 64,
        "feature_schema_hash": "d" * 64,
        "vocabulary_manifest": {"source_sha256": "e" * 64},
        "collector_source_sha256": "f" * 64,
        "collector_source_snapshot_path": str(
            (run / "collector_source_snapshot.py").resolve()
        ),
        "seat_schedule": "seat=(game_index//opponent_count)%2",
        "opponent_schedule": "opponent_ids[game_index%opponent_count]",
        "matchup_cap_fraction": 0.25,
    }
    _write_json(run / "collection_contract.json", contract)

    record = records / "game-000000.jsonl"
    record.write_text('{"record":"fixture"}\n', encoding="utf-8")
    sidecar = {
        "schema_version": "specialist-teacher-collection-game-result-v2",
        "game_index": 0,
        "seed": 700,
        "seat": 0,
        "opponent_id": "opponent-a",
        "episode_id_hash": hashlib.sha256(
            f"mage_ptcg:teacher-episode:v1\0fixture-v2\0{0}\0{700}".encode("utf-8")
        ).hexdigest(),
        "status": "DONE",
        "outcome": "win",
        "record_path": str(record.resolve()),
        "record_sha256": _sha(record),
        "record_count": 1,
        "unlabelled": 0,
        "omissions": [],
        "detail": "",
        "subject_deck_sha256": _sha(subject_deck),
        "teacher_policy_sha256": _sha(teacher_policy),
        "permission_manifest_id": permission["permission_manifest_id"],
    }
    _write_json(run / "game-results" / "game-000000.result.json", sidecar)
    _write_json(
        run / "game-attempts" / "game-000000-attempt-0001.json",
        {**sidecar, "attempt_ordinal": 1},
    )
    (run / "omissions.jsonl").write_bytes(b"")

    manifest = {
        "schema_version": "specialist-teacher-dataset-manifest-v2",
        "run_name": "fixture-v2",
        "archetype_id": "archaludon",
        "subject_deck_csv_path": str(subject_deck.resolve()),
        "subject_deck_file_sha256": _sha(subject_deck),
        "base_seed": 700,
        "max_steps": 2000,
        "source_commit": "b" * 40,
        "teacher_id": "teacher-x",
        "teacher_policy_hash": _sha(teacher_policy),
        "teacher_deck_file_sha256": _sha(teacher_deck),
        "teacher_source_kind": source_kind,
        "teacher_usage_boundary": "local_eval_only",
        "permission_manifest": permission,
        "permission_content_hash": permission["content_hash"],
        "permission_trusted_bytes_sha256": hashlib.sha256(
            canonical_json_bytes_v2(permission)
        ).hexdigest(),
        "derivation_decision_ref": "docs/decision.md",
        "opponent_ids": ["opponent-a"],
        "games_requested": 1,
        "games_completed": 1,
        "games_faulted": 0,
        "games_other_status": [],
        "records_written": 1,
        "decisions_unlabelled": 0,
        "outcome_counts": {"win": 1},
        "seat_counts": {"subject_first": 1, "subject_second": 0},
        "records_dir": str(records.resolve()),
        "matchup_record_counts": {"opponent-a": 1},
        "matchup_cap_fraction": 0.25,
        "omissions_path": str((run / "omissions.jsonl").resolve()),
        "collection_contract_path": str((run / "collection_contract.json").resolve()),
        "collection_contract_sha256": _sha(run / "collection_contract.json"),
        "collector_source_snapshot_path": str(
            (run / "collector_source_snapshot.py").resolve()
        ),
        "collector_source_sha256": "f" * 64,
        "omissions_sha256": _sha(run / "omissions.jsonl"),
        "game_result_sidecars": 1,
        "game_attempts_total": 1,
        "game_attempts_non_done": 0,
    }
    (run / "collector_source_snapshot.py").write_bytes(b"fixture collector v2\n")
    collector_sha = _sha(run / "collector_source_snapshot.py")
    contract["collector_source_sha256"] = collector_sha
    _write_json(run / "collection_contract.json", contract)
    manifest["collection_contract_sha256"] = _sha(run / "collection_contract.json")
    manifest["collector_source_sha256"] = collector_sha
    _write_json(run / "teacher_dataset_manifest.json", manifest)
    return run, manifest


def _build_snapshot(tmp_path: Path):
    from mage_ptcg.meta_specialist.training_snapshot_v1 import build_training_snapshot_v1

    path, records, manifest, permission, trusted, vocabulary = _qualified_dataset(tmp_path)
    snapshot = build_training_snapshot_v1(
        path,
        manifest=manifest,
        vocabulary=vocabulary,
        trusted_permissions=trusted,
        qualification_time_utc="2026-08-02T00:00:00Z",
    )
    return snapshot, path, records, manifest, permission, trusted, vocabulary


def _rewrite_manifest(run: Path, mutate) -> None:
    path = run / "teacher_dataset_manifest.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    mutate(value)
    _write_json(path, value)


def test_v2_preflight_closes_contract_ledgers_assets_and_permission(tmp_path: Path) -> None:
    run, manifest = _make_collection(tmp_path)

    result = _preflight_collection_v2(run, expected_archetype_id="archaludon")

    assert result.collection == manifest
    assert result.source_kind == "pooled_external_submission_agent"
    assert result.permission_content_hash == manifest["permission_manifest"]["content_hash"]
    kinds = {row["kind"] for row in result.provenance_artifacts}
    assert kinds == {
        "teacher_collection_manifest_v2",
        "teacher_collection_contract_v2",
        "teacher_collection_omissions_v2",
        "teacher_collector_source_snapshot_v2",
        "teacher_permission_trusted_bytes_v1",
        "teacher_source_kind:pooled_external_submission_agent",
    }


@pytest.mark.parametrize("failure", ["legacy", "missing-sidecar", "non-done", "missing-attempt"])
def test_v2_preflight_rejects_incomplete_or_legacy_collection(
    tmp_path: Path, failure: str
) -> None:
    run, _manifest = _make_collection(tmp_path)
    if failure == "legacy":
        _rewrite_manifest(run, lambda value: value.__setitem__(
            "schema_version", "specialist-teacher-dataset-manifest-v1"
        ))
    elif failure == "missing-sidecar":
        (run / "game-results" / "game-000000.result.json").unlink()
    elif failure == "non-done":
        path = run / "game-results" / "game-000000.result.json"
        row = json.loads(path.read_text(encoding="utf-8"))
        row.update(status="faulted", outcome=None, record_sha256=None, record_count=0)
        (run / "records" / "game-000000.jsonl").unlink()
        _write_json(path, row)
    else:
        (run / "game-attempts" / "game-000000-attempt-0001.json").unlink()

    with pytest.raises(CollectionSealPreflightV2Error):
        _preflight_collection_v2(run, expected_archetype_id="archaludon")


@pytest.mark.parametrize(
    "failure", ["contract", "collector-source", "omissions", "record-sha", "record-count"]
)
def test_v2_preflight_rejects_tampered_trusted_files(
    tmp_path: Path, failure: str
) -> None:
    run, _manifest = _make_collection(tmp_path)
    if failure == "contract":
        path = run / "collection_contract.json"
        path.write_bytes(path.read_bytes() + b" ")
    elif failure == "collector-source":
        (run / "collector_source_snapshot.py").write_bytes(b"tampered collector\n")
    elif failure == "omissions":
        (run / "omissions.jsonl").write_text('{"reason":"late"}\n', encoding="utf-8")
    elif failure == "record-sha":
        (run / "records" / "game-000000.jsonl").write_text(
            '{"record":"tampered"}\n', encoding="utf-8"
        )
    else:
        path = run / "game-results" / "game-000000.result.json"
        row = json.loads(path.read_text(encoding="utf-8"))
        row["record_count"] = 2
        _write_json(path, row)

    with pytest.raises(CollectionSealPreflightV2Error):
        _preflight_collection_v2(run, expected_archetype_id="archaludon")


@pytest.mark.parametrize("failure", ["subject", "teacher", "source-kind", "permission-content"])
def test_v2_preflight_rejects_asset_or_permission_mismatch(
    tmp_path: Path, failure: str
) -> None:
    run, manifest = _make_collection(tmp_path)
    if failure == "subject":
        Path(manifest["subject_deck_csv_path"]).write_text("3\n" * 60, encoding="utf-8")
    elif failure == "teacher":
        pool = Path(json.loads((run / "collection_contract.json").read_text())["pool_root"])
        (pool / "teacher-x" / "deck.csv").write_text("4\n" * 60, encoding="utf-8")
    elif failure == "source-kind":
        _rewrite_manifest(run, lambda value: value.__setitem__(
            "teacher_source_kind", "team_internal_agent"
        ))
    else:
        def mutate(value: dict) -> None:
            value["permission_manifest"]["content_hash"] = "0" * 64
        _rewrite_manifest(run, mutate)

    with pytest.raises(CollectionSealPreflightV2Error):
        _preflight_collection_v2(run, expected_archetype_id="archaludon")


def test_provenance_binding_preserves_standalone_snapshot_reader(tmp_path: Path) -> None:
    run, _manifest = _make_collection(tmp_path / "collection")
    preflight = _preflight_collection_v2(run, expected_archetype_id="archaludon")
    snapshot, *_ = _build_snapshot(tmp_path / "snapshot")
    old_snapshot_id = snapshot["snapshot_id"]

    bound = _bind_collection_provenance_to_snapshot_v2(
        snapshot, preflight.provenance_artifacts
    )
    path = tmp_path / "bound.json"
    atomic_write_training_snapshot_v1(path, bound)
    reread = read_training_snapshot_v1(path)

    assert reread["snapshot_id"] != old_snapshot_id
    for row in preflight.provenance_artifacts:
        assert row in reread["source_artifacts"]


def test_provenance_binding_updates_every_shard_and_index(tmp_path: Path) -> None:
    run, _manifest = _make_collection(tmp_path / "collection")
    preflight = _preflight_collection_v2(run, expected_archetype_id="archaludon")
    snapshot, *_ = _build_snapshot(tmp_path / "snapshot")
    shard = copy.deepcopy(snapshot)
    shard["schema_version"] = TRAINING_SHARD_SCHEMA_V1
    shard["snapshot_id"] = _snapshot_identity(shard)
    shard["content_hash"] = _snapshot_content_hash(shard)
    output = tmp_path / "sharded"
    output.mkdir()
    shard_path = output / "snapshot-0000.json"
    atomic_write_training_snapshot_v1(shard_path, shard)
    index = {
        "schema_version": SHARD_INDEX_SCHEMA_V1,
        "dataset_snapshot_sha256": shard["dataset_snapshot_sha256"],
        "manifest_id": shard["manifest_id"],
        "dataset_chunks": [],
        "source_artifacts": list(shard["source_artifacts"]),
        "examples_total": len(shard["examples"]),
        "split_names": list(shard["split_names"]),
        "split_weights": list(shard["split_weights"]),
        "split_counts": dict(shard["split_counts"]),
        "duplicate_cap": dict(shard["duplicate_cap"]),
        "shards": [{
            "path": shard_path.name,
            "snapshot_id": shard["snapshot_id"],
            "examples": len(shard["examples"]),
            "split_counts": dict(shard["split_counts"]),
        }],
    }
    (output / "snapshot_index.json").write_bytes(canonical_json_bytes_v2(index))

    rebound = _bind_collection_provenance_to_sharded_output_v2(
        output, index, preflight.provenance_artifacts
    )

    bound_shard = read_training_snapshot_v1(shard_path)
    assert rebound["shards"][0]["snapshot_id"] == bound_shard["snapshot_id"]
    for row in preflight.provenance_artifacts:
        assert row in rebound["source_artifacts"]
        assert row in bound_shard["source_artifacts"]
    assert json.loads((output / "snapshot_index.json").read_text()) == rebound
