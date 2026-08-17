from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil

import pytest


ROOT = Path(__file__).resolve().parents[2]


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _catalog_sha(payload: dict[str, object]) -> str:
    return hashlib.sha256(_canonical({key: value for key, value in payload.items() if key != "catalog_sha256"})).hexdigest()


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_canonical(payload) + b"\n")


def _ready_v2_fixture(tmp_path: Path) -> dict[str, object]:
    """Build a tiny, real v2 collection whose 96 games each hold one decision."""
    from mage_ptcg.meta_specialist.actor_visible_v2 import (
        build_actor_visible_decision_state_v2,
    )
    from mage_ptcg.meta_specialist.card_vocabulary_registry_v1 import (
        load_production_card_vocabulary_v1,
    )
    from mage_ptcg.meta_specialist.collect_teacher_records_v1 import (
        _write_game_result_sidecar_v1,
    )
    from mage_ptcg.meta_specialist.derived_teacher_catalog_v1 import (
        DECISION_RELATIVE_PATH,
    )
    from mage_ptcg.meta_specialist.local_dataset_v2 import (
        _record_content_hash,
        _record_id,
        atomic_write_local_dataset_v2,
        build_local_dataset_manifest_v2,
        build_local_record_v2,
        build_trusted_permission_set_v1,
        canonical_json_bytes_v2,
        derive_complete_action_id_v1,
        make_source_permission_manifest_v1,
    )
    from mage_ptcg.meta_specialist.training_snapshot_v1 import (
        _snapshot_content_hash,
        _snapshot_identity,
        build_sharded_training_snapshots_v1,
    )
    root = tmp_path / "repo"
    teacher_id = "fixture_teacher"
    source_kind = "pooled_external_submission_agent"
    archetype = "archaludon"
    asset_root = root / "opponents" / teacher_id
    asset_root.mkdir(parents=True)
    policy_path = asset_root / "main.py"
    policy_path.write_text("def agent(observation):\n    return 0\n", encoding="utf-8")
    deck_path = asset_root / "deck.csv"
    deck_path.write_text("id,count\n1,60\n", encoding="utf-8")
    policy_sha = _file_sha(policy_path)
    deck_sha = _file_sha(deck_path)

    decision_path = root / DECISION_RELATIVE_PATH
    decision_path.parent.mkdir(parents=True)
    decision_path.write_text(
        f"{teacher_id}\nderivation_qualified\ntraining-local\n", encoding="utf-8"
    )

    permission = make_source_permission_manifest_v1(
        artifact_sha256=policy_sha,
        source_kind=source_kind,
        allowed_usages=("training-local",),
        revision=policy_sha[:16],
        issuer=DECISION_RELATIVE_PATH,
        valid_from_utc=None,
        expires_at_utc=None,
    )
    permission_bytes = canonical_json_bytes_v2(permission)
    trusted = build_trusted_permission_set_v1((permission_bytes,))
    vocabulary = load_production_card_vocabulary_v1()
    player = {
        "active": [], "asleep": False, "bench": [], "benchMax": 5,
        "burned": False, "confused": False, "deckCount": 60, "discard": [],
        "hand": [], "handCount": 0, "paralyzed": False, "poisoned": False,
        "prize": [],
    }
    observation = {
        "current": {
            "energyAttached": False, "firstPlayer": 0, "looking": None,
            "players": [player, {**player, "hand": None}], "result": -1,
            "retreated": False, "stadium": [], "stadiumPlayed": False,
            "supporterPlayed": False, "turn": 2, "turnActionCount": 0,
            "yourIndex": 0,
        },
        "select": {
            "context": 41, "contextCard": None, "deck": None, "effect": None,
            "maxCount": 1, "minCount": 1, "option": [{"type": 1}, {"type": 2}],
            "remainDamageCounter": 0, "remainEnergyCost": 0, "type": 9,
        },
        "step": 0,
    }
    state = build_actor_visible_decision_state_v2(observation)
    selected = (state.legal_actions[0].local_action_id,)
    source = {
        "kind": source_kind,
        "artifact_sha256": policy_sha,
        "synthetic": False,
        "synthetic_fields": [],
        "training_eligible": True,
        "usage_class": "qualified_training",
        "permission_manifest_id": permission["permission_manifest_id"],
    }
    bootstrap = build_local_record_v2(
        state=state,
        vocabulary=vocabulary,
        episode_id_hash="0" * 64,
        decision_index=0,
        selection=selected,
        behavior={
            "status": "unavailable",
            "reason": "external teacher exposes no policy distribution",
        },
        teacher={"status": "unavailable", "reason": "bootstrap"},
        student={"status": "fallback", "selection": [], "scores": [], "reason": "none"},
        source=source,
        provenance={"source_record_ordinal": 0},
    )
    complete = derive_complete_action_id_v1(
        decision_id=bootstrap["decision_id"],
        selection_type=state.information_view.selection_type,
        selection_context=state.information_view.selection_context,
        selection=selected,
    )
    first = build_local_record_v2(
        state=state,
        vocabulary=vocabulary,
        episode_id_hash=hashlib.sha256(b"episode-0").hexdigest(),
        decision_index=0,
        selection=selected,
        behavior={
            "status": "unavailable",
            "reason": "external teacher exposes no policy distribution",
        },
        teacher={
            "status": "available",
            "teacher_id": teacher_id,
            "teacher_revision": policy_sha[:16],
            "input_id": bootstrap["model_input_id"],
            "target_kind": "hard_selection",
            "quality_weight": 0.1,
            "value_target": 1.0,
            "mass_rows": [{
                "complete_action_id": complete,
                "selection": list(selected),
                "weight": 1,
            }],
        },
        student={"status": "fallback", "selection": [], "scores": [], "reason": "none"},
        source=source,
        provenance={"source_record_ordinal": 0},
    )
    records: list[dict[str, object]] = []
    for game_index in range(96):
        row = json.loads(json.dumps(first))
        row["episode_id_hash"] = hashlib.sha256(
            f"episode-{game_index}".encode("ascii")
        ).hexdigest()
        row["decision_index"] = game_index
        row["record_id"] = _record_id(
            decision_id=row["decision_id"],
            episode_id_hash=row["episode_id_hash"],
            decision_index=game_index,
        )
        row["provenance"]["source_record_ordinal"] = game_index
        row["content_hash"] = _record_content_hash(row)
        records.append(row)

    run_root = root / "runs" / "fixture-v2"
    run_root.mkdir(parents=True)
    dataset_path = run_root / "dataset-0000.jsonl"
    dataset_manifest = build_local_dataset_manifest_v2(
        records=tuple(records),
        # The sealer's dataset-environment contract is deliberately distinct
        # from the production card-vocabulary environment carried by shards.
        environment_version="cabt-local-v1",
        deck_fingerprint=deck_sha,
        trusted_permissions=trusted,
    )
    atomic_write_local_dataset_v2(
        dataset_path, records=tuple(records), manifest=dataset_manifest
    )
    build_sharded_training_snapshots_v1(
        dataset_path,
        manifest=dataset_manifest,
        vocabulary=vocabulary,
        trusted_permissions=trusted,
        qualification_time_utc="2026-08-13T00:00:00Z",
        output_dir=run_root,
        shard_max_examples=64,
    )

    records_dir = run_root / "records"
    records_dir.mkdir()
    base_seed = 7000
    for game_index, record in enumerate(records):
        record_path = records_dir / f"game-{game_index:06d}.jsonl"
        record_path.write_bytes(_canonical(record) + b"\n")
        _write_game_result_sidecar_v1(
            records_dir=records_dir,
            game_index=game_index,
            seed=base_seed + game_index,
            seat=game_index % 2,
            opponent_id="fixture_opponent",
            episode_id_hash=record["episode_id_hash"],
            status="DONE",
            outcome="win",
            record_path=record_path,
            record_count=1,
            unlabelled=0,
            omissions=(),
            detail="",
            subject_deck_sha256=deck_sha,
            teacher_policy_sha256=policy_sha,
            permission_manifest_id=permission["permission_manifest_id"],
        )

    omissions_path = run_root / "omissions.jsonl"
    omissions_path.write_bytes(b"")
    pool_manifest = root / "opponents" / "pool_manifest.json"
    _write_json(pool_manifest, {"schema_version": "fixture-pool-v1"})
    engine_path = root / "scripts" / "test_sim.py"
    engine_path.parent.mkdir(parents=True)
    engine_path.write_text("def run_match():\n    pass\n", encoding="utf-8")
    collector_path = Path(
        __import__(
            "mage_ptcg.meta_specialist.collect_teacher_records_v1",
            fromlist=["__file__"],
        ).__file__
    )
    collector_snapshot_path = run_root / "collector_source_snapshot.py"
    shutil.copyfile(collector_path, collector_snapshot_path)
    permission_raw_sha = hashlib.sha256(permission_bytes).hexdigest()
    teacher_asset = {
        "opponent_id": teacher_id,
        "policy_sha256": policy_sha,
        "deck_file_sha256": deck_sha,
        "canonical_deck_hash": hashlib.sha256(b"canonical-deck").hexdigest(),
        "source": "public",
        "usage_boundary": "local_eval_only",
    }
    opponent_asset = {
        **teacher_asset,
        "opponent_id": "fixture_opponent",
    }
    contract = {
        "schema_version": "specialist-teacher-collection-contract-v2",
        "run_name": "fixture-v2",
        "archetype_id": archetype,
        "subject_deck_csv_path": str(deck_path.resolve()),
        "subject_deck_file_sha256": deck_sha,
        "teacher": teacher_asset,
        "teacher_source_kind": source_kind,
        "opponent_ids": ["fixture_opponent"],
        "opponents": [opponent_asset],
        "games_requested": 96,
        "base_seed": base_seed,
        "max_steps": 2000,
        "source_commit": "0" * 40,
        "decision_ref": DECISION_RELATIVE_PATH,
        "permission_manifest_id": permission["permission_manifest_id"],
        "permission_content_hash": permission["content_hash"],
        "permission_trusted_bytes_sha256": permission_raw_sha,
        "allowed_usages": ["training-local"],
        "pool_root": str((root / "opponents").resolve()),
        "pool_manifest_sha256": _file_sha(pool_manifest),
        "engine_entry_point": str(engine_path.resolve()),
        "engine_source_sha256": _file_sha(engine_path),
        "feature_schema_hash": vocabulary.to_manifest_dict()["vocabulary_schema_hash"],
        "vocabulary_manifest": vocabulary.to_manifest_dict(),
        "collector_source_sha256": _file_sha(collector_path),
        "collector_source_snapshot_path": str(collector_snapshot_path.resolve()),
        "seat_schedule": "seat=(game_index//opponent_count)%2",
        "opponent_schedule": "opponent_ids[game_index%opponent_count]",
        "matchup_cap_fraction": 0.25,
    }
    # The production contract stores the actor-visible feature hash, not the
    # vocabulary schema hash. Keep this literal independent of catalog code.
    from mage_ptcg.meta_specialist.actor_visible_features_v1 import FEATURE_SCHEMA_HASH_V1
    contract["feature_schema_hash"] = FEATURE_SCHEMA_HASH_V1
    contract_path = run_root / "collection_contract.json"
    _write_json(contract_path, contract)

    teacher_manifest = {
        "schema_version": "specialist-teacher-dataset-manifest-v2",
        "run_name": "fixture-v2",
        "archetype_id": archetype,
        "subject_deck_csv_path": str(deck_path.resolve()),
        "subject_deck_file_sha256": deck_sha,
        "base_seed": base_seed,
        "max_steps": 2000,
        "source_commit": "0" * 40,
        "teacher_id": teacher_id,
        "teacher_policy_hash": policy_sha,
        "teacher_deck_file_sha256": deck_sha,
        "teacher_source_kind": source_kind,
        "teacher_usage_boundary": "local_eval_only",
        "permission_manifest": permission,
        "derivation_decision_ref": DECISION_RELATIVE_PATH,
        "opponent_ids": ["fixture_opponent"],
        "games_requested": 96,
        "games_completed": 96,
        "games_faulted": 0,
        "games_other_status": [],
        "records_written": 96,
        "decisions_unlabelled": 0,
        "outcome_counts": {"win": 96},
        "seat_counts": {"subject_first": 48, "subject_second": 48},
        "records_dir": str(records_dir.resolve()),
        "matchup_record_counts": {"fixture_opponent": 96},
        "matchup_cap_fraction": 0.25,
        "omissions_path": str(omissions_path.resolve()),
        "omissions_sha256": _file_sha(omissions_path),
        "collection_contract_path": str(contract_path.resolve()),
        "collection_contract_sha256": _file_sha(contract_path),
        "collector_source_snapshot_path": str(collector_snapshot_path.resolve()),
        "collector_source_sha256": _file_sha(collector_snapshot_path),
        "permission_trusted_bytes_sha256": permission_raw_sha,
        "permission_content_hash": permission["content_hash"],
        "game_result_sidecars": 96,
        "game_attempts_total": 96,
        "game_attempts_non_done": 0,
    }
    manifest_path = run_root / "teacher_dataset_manifest.json"
    _write_json(manifest_path, teacher_manifest)
    hardened_sources = [
        {"kind": source_kind, "artifact_sha256": policy_sha},
        {"kind": "teacher_collection_contract_v2", "artifact_sha256": _file_sha(contract_path)},
        {"kind": "teacher_collection_manifest_v2", "artifact_sha256": _file_sha(manifest_path)},
        {"kind": "teacher_collection_omissions_v2", "artifact_sha256": _file_sha(omissions_path)},
        {"kind": "teacher_collector_source_snapshot_v2", "artifact_sha256": _file_sha(collector_snapshot_path)},
        {"kind": "teacher_permission_trusted_bytes_v1", "artifact_sha256": permission_raw_sha},
        {"kind": f"teacher_source_kind:{source_kind}", "artifact_sha256": hashlib.sha256(source_kind.encode("utf-8")).hexdigest()},
    ]
    hardened_sources.sort(key=lambda row: (row["kind"], row["artifact_sha256"]))
    index_path = run_root / "snapshot_index.json"
    snapshot_index = json.loads(index_path.read_text(encoding="utf-8"))
    snapshot_index["source_artifacts"] = hardened_sources
    for shard_row in snapshot_index["shards"]:
        shard_path = run_root / shard_row["path"]
        shard = json.loads(shard_path.read_text(encoding="utf-8"))
        shard["source_artifacts"] = hardened_sources
        shard["snapshot_id"] = _snapshot_identity(shard)
        shard["content_hash"] = _snapshot_content_hash(shard)
        shard_path.write_bytes(_canonical(shard))
        shard_row["snapshot_id"] = shard["snapshot_id"]
    index_path.write_bytes(_canonical(snapshot_index))
    spec = {
        "teacher_id": teacher_id,
        "archetype": archetype,
        "source_kind": source_kind,
        "collection_manifest": manifest_path.relative_to(root).as_posix(),
        "snapshot_index": (run_root / "snapshot_index.json").relative_to(root).as_posix(),
    }
    return {
        "root": root,
        "run_root": run_root,
        "spec": spec,
        "policy": {
            "path": policy_path.relative_to(root).as_posix(),
            "sha256": policy_sha,
        },
        "deck": {
            "path": deck_path.relative_to(root).as_posix(),
            "sha256": deck_sha,
        },
        "manifest_path": manifest_path,
        "contract_path": contract_path,
        "omissions_path": omissions_path,
        "sidecar_path": run_root / "game-results" / "game-000000.result.json",
        "attempt_path": run_root / "game-attempts" / "game-000000-attempt-0001.json",
    }


def _ready_collection(fixture: dict[str, object]) -> dict[str, object]:
    from mage_ptcg.meta_specialist.derived_teacher_catalog_v1 import _ready_collection

    return _ready_collection(
        fixture["root"],
        fixture["spec"],
        fixture["policy"],
        fixture["deck"],
    )


def test_ready_v2_collection_revalidates_primary_artifacts(tmp_path: Path) -> None:
    fixture = _ready_v2_fixture(tmp_path)

    collection = _ready_collection(fixture)

    assert collection["status"] == "READY"
    assert collection["game_counts"] == {
        "requested": 96,
        "completed": 96,
        "faulted": 0,
        "unlabelled": 0,
        "other_status_count": 0,
    }
    assert collection["snapshot_index"]["examples_total"] == 96


def test_legacy_v1_collection_is_explicitly_blocked_from_ready(tmp_path: Path) -> None:
    from mage_ptcg.meta_specialist.derived_teacher_catalog_v1 import (
        DerivedTeacherCatalogError,
    )

    fixture = _ready_v2_fixture(tmp_path)
    path = fixture["manifest_path"]
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["schema_version"] = "specialist-teacher-dataset-manifest-v1"
    _write_json(path, manifest)

    with pytest.raises(DerivedTeacherCatalogError, match="LEGACY_V1_BLOCKED"):
        _ready_collection(fixture)


def test_ready_rejects_a_tampered_snapshot_shard_even_when_index_is_unchanged(
    tmp_path: Path,
) -> None:
    from mage_ptcg.meta_specialist.derived_teacher_catalog_v1 import (
        DerivedTeacherCatalogError,
    )

    fixture = _ready_v2_fixture(tmp_path)
    index = json.loads(
        (fixture["run_root"] / "snapshot_index.json").read_text(encoding="utf-8")
    )
    shard = fixture["run_root"] / index["shards"][0]["path"]
    body = bytearray(shard.read_bytes())
    body[len(body) // 2] ^= 1
    shard.write_bytes(body)

    with pytest.raises(DerivedTeacherCatalogError, match="snapshot shard"):
        _ready_collection(fixture)


def test_ready_rejects_rehashed_contract_that_disagrees_with_manifest(
    tmp_path: Path,
) -> None:
    from mage_ptcg.meta_specialist.derived_teacher_catalog_v1 import (
        DerivedTeacherCatalogError,
    )

    fixture = _ready_v2_fixture(tmp_path)
    contract_path = fixture["contract_path"]
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    contract["base_seed"] += 1
    _write_json(contract_path, contract)
    manifest_path = fixture["manifest_path"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["collection_contract_sha256"] = _file_sha(contract_path)
    _write_json(manifest_path, manifest)

    with pytest.raises(DerivedTeacherCatalogError, match="collection contract.*base_seed"):
        _ready_collection(fixture)


def test_ready_rejects_omissions_outside_the_expected_collection_root(
    tmp_path: Path,
) -> None:
    from mage_ptcg.meta_specialist.derived_teacher_catalog_v1 import (
        DerivedTeacherCatalogError,
    )

    fixture = _ready_v2_fixture(tmp_path)
    outside = fixture["root"] / "outside-omissions.jsonl"
    outside.write_bytes(b"")
    manifest_path = fixture["manifest_path"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["omissions_path"] = str(outside.resolve())
    manifest["omissions_sha256"] = _file_sha(outside)
    _write_json(manifest_path, manifest)

    with pytest.raises(DerivedTeacherCatalogError, match="omissions.*collection root"):
        _ready_collection(fixture)


def test_ready_rejects_permission_content_hash_even_with_manifest_file_rehashed(
    tmp_path: Path,
) -> None:
    from mage_ptcg.meta_specialist.derived_teacher_catalog_v1 import (
        DerivedTeacherCatalogError,
    )

    fixture = _ready_v2_fixture(tmp_path)
    manifest_path = fixture["manifest_path"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["permission_manifest"]["content_hash"] = "0" * 64
    _write_json(manifest_path, manifest)

    with pytest.raises(DerivedTeacherCatalogError, match="permission.*content_hash"):
        _ready_collection(fixture)


def test_ready_rejects_manifest_source_kind_that_disagrees_with_catalog(
    tmp_path: Path,
) -> None:
    from mage_ptcg.meta_specialist.derived_teacher_catalog_v1 import (
        DerivedTeacherCatalogError,
    )

    fixture = _ready_v2_fixture(tmp_path)
    manifest_path = fixture["manifest_path"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["teacher_source_kind"] = "team_internal_agent"
    _write_json(manifest_path, manifest)

    with pytest.raises(DerivedTeacherCatalogError, match="source_kind"):
        _ready_collection(fixture)


def test_ready_rejects_manifest_or_contract_unknown_fields(tmp_path: Path) -> None:
    from mage_ptcg.meta_specialist.derived_teacher_catalog_v1 import (
        DerivedTeacherCatalogError,
    )

    fixture = _ready_v2_fixture(tmp_path)
    manifest_path = fixture["manifest_path"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["unreviewed_extension"] = True
    _write_json(manifest_path, manifest)

    with pytest.raises(DerivedTeacherCatalogError, match="manifest.*closed schema"):
        _ready_collection(fixture)


def test_ready_rejects_sidecar_source_binding_that_disagrees_with_manifest(
    tmp_path: Path,
) -> None:
    from mage_ptcg.meta_specialist.derived_teacher_catalog_v1 import (
        DerivedTeacherCatalogError,
    )

    fixture = _ready_v2_fixture(tmp_path)
    sidecar_path = fixture["sidecar_path"]
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    sidecar["teacher_policy_sha256"] = "0" * 64
    _write_json(sidecar_path, sidecar)

    with pytest.raises(DerivedTeacherCatalogError, match="game sidecar.*teacher policy"):
        _ready_collection(fixture)


def test_ready_rejects_attempt_count_that_is_only_declared_in_manifest(
    tmp_path: Path,
) -> None:
    from mage_ptcg.meta_specialist.derived_teacher_catalog_v1 import (
        DerivedTeacherCatalogError,
    )

    fixture = _ready_v2_fixture(tmp_path)
    fixture["attempt_path"].unlink()

    with pytest.raises(DerivedTeacherCatalogError, match="game attempts.*count"):
        _ready_collection(fixture)


def test_ready_rejects_same_count_but_different_omission_ledger_rows(
    tmp_path: Path,
) -> None:
    from mage_ptcg.meta_specialist.derived_teacher_catalog_v1 import (
        DerivedTeacherCatalogError,
    )

    fixture = _ready_v2_fixture(tmp_path)
    sidecar_path = fixture["sidecar_path"]
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    sidecar["unlabelled"] = 1
    sidecar["omissions"] = [{
        "decision_index": 3,
        "episode_id_hash": "1" * 64,
        "teacher": {"status": "unavailable", "reason": "sidecar reason"},
    }]
    _write_json(sidecar_path, sidecar)
    attempt = {**sidecar, "attempt_ordinal": 1}
    _write_json(fixture["attempt_path"], attempt)
    ledger_row = {
        "decision_index": 3,
        "episode_id_hash": "1" * 64,
        "teacher": {"status": "unavailable", "reason": "different ledger reason"},
    }
    omissions_path = fixture["omissions_path"]
    omissions_path.write_bytes(_canonical(ledger_row) + b"\n")
    manifest_path = fixture["manifest_path"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["decisions_unlabelled"] = 1
    manifest["omissions_sha256"] = _file_sha(omissions_path)
    _write_json(manifest_path, manifest)

    with pytest.raises(DerivedTeacherCatalogError, match="omission ledger rows"):
        _ready_collection(fixture)


def test_ready_rejects_raw_record_source_kind_even_when_record_self_hash_is_updated(
    tmp_path: Path,
) -> None:
    from mage_ptcg.meta_specialist.derived_teacher_catalog_v1 import (
        DerivedTeacherCatalogError,
    )
    from mage_ptcg.meta_specialist.local_dataset_v2 import _record_content_hash
    from mage_ptcg.meta_specialist.training_snapshot_v1 import (
        _snapshot_content_hash,
        _snapshot_identity,
        corpus_dataset_sha256_v1,
    )

    fixture = _ready_v2_fixture(tmp_path)
    dataset = fixture["run_root"] / "dataset-0000.jsonl"
    rows = [json.loads(line) for line in dataset.read_text(encoding="utf-8").splitlines()]
    rows[0]["source"]["kind"] = "team_internal_agent"
    rows[0]["content_hash"] = _record_content_hash(rows[0])
    dataset.write_bytes(b"".join(_canonical(row) + b"\n" for row in rows))
    index_path = fixture["run_root"] / "snapshot_index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    chunk_sha = _file_sha(dataset)
    index["dataset_chunks"][0]["dataset_snapshot_sha256"] = chunk_sha
    corpus_sha = corpus_dataset_sha256_v1([chunk_sha])
    index["dataset_snapshot_sha256"] = corpus_sha
    for shard_row in index["shards"]:
        shard_path = fixture["run_root"] / shard_row["path"]
        shard = json.loads(shard_path.read_text(encoding="utf-8"))
        shard["dataset_snapshot_sha256"] = corpus_sha
        shard["snapshot_id"] = _snapshot_identity(shard)
        shard["content_hash"] = _snapshot_content_hash(shard)
        shard_path.write_bytes(_canonical(shard))
        shard_row["snapshot_id"] = shard["snapshot_id"]
    index_path.write_bytes(_canonical(index))

    with pytest.raises(DerivedTeacherCatalogError, match="raw record source_kind"):
        _ready_collection(fixture)


def _build_v2_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> tuple[dict[str, object], Path, Path]:
    import mage_ptcg.meta_specialist.derived_teacher_catalog_v1 as catalog_module

    fixture = _ready_v2_fixture(tmp_path)
    monkeypatch.setattr(catalog_module, "_TEACHERS", (fixture["spec"],))
    output = tmp_path / "catalog.json"
    payload = catalog_module.build_derived_teacher_catalog_v1(
        fixture["root"], output_path=output
    )
    return payload, output, fixture["root"]


def test_build_catalog_closes_derived_weight_permission_without_granting_submission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mage_ptcg.meta_specialist.derived_teacher_catalog_v1 import (
        build_derived_teacher_catalog_v1,
        verify_derived_teacher_catalog_v1,
    )

    payload, output, root = _build_v2_catalog(tmp_path, monkeypatch)
    verified = verify_derived_teacher_catalog_v1(output, root)

    assert verified == payload
    assert payload["schema_version"] == "meta-specialist-derived-teacher-catalog-v2"
    assert payload["derived_weights_allowed"] is True
    assert payload["training_authority"] is False
    assert payload["promotion_authority"] is False
    assert payload["submission_authority"] is False
    teachers = {row["teacher_id"]: row for row in payload["teachers"]}
    assert set(teachers) == {"fixture_teacher"}
    assert all(row["collection"]["status"] == "READY" for row in teachers.values())
    expected_examples = {"fixture_teacher": 96}
    for teacher_id, examples in expected_examples.items():
        collection = teachers[teacher_id]["collection"]
        assert collection["game_counts"] == {
            "requested": 96, "completed": 96, "faulted": 0, "unlabelled": 0, "other_status_count": 0,
        }
        assert collection["seat_counts"] == {"subject_first": 48, "subject_second": 48}
        assert collection["snapshot_index"]["examples_total"] == examples
        assert sum(collection["snapshot_index"]["split_counts"].values()) == examples
    for row in teachers.values():
        assert row["teacher_code_submission_allowed"] is False
        assert row["deck_submission_allowed"] is False
        assert row["teacher_usage_boundary"] == "local_eval_only"
        assert row["allowed_usages"] == ["training-local"]
        assert "split" not in row


def test_catalog_verifier_fails_closed_when_rehashed_policy_binding_is_corrupted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mage_ptcg.meta_specialist.derived_teacher_catalog_v1 import (
        DerivedTeacherCatalogError,
        build_derived_teacher_catalog_v1,
        verify_derived_teacher_catalog_v1,
    )

    payload, output, root = _build_v2_catalog(tmp_path, monkeypatch)
    payload["teachers"][0]["policy"]["sha256"] = "0" * 64
    payload["catalog_sha256"] = _catalog_sha(payload)
    output.write_bytes(_canonical(payload))

    with pytest.raises(DerivedTeacherCatalogError, match="policy SHA-256"):
        verify_derived_teacher_catalog_v1(output, root)


def test_catalog_rejects_evaluation_split_even_when_self_hash_is_recomputed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mage_ptcg.meta_specialist.derived_teacher_catalog_v1 import (
        DerivedTeacherCatalogError,
        build_derived_teacher_catalog_v1,
        verify_derived_teacher_catalog_v1,
    )

    payload, output, root = _build_v2_catalog(tmp_path, monkeypatch)
    payload["evaluation_split"] = "META_DEV"
    payload["catalog_sha256"] = _catalog_sha(payload)
    output.write_bytes(_canonical(payload))

    with pytest.raises(DerivedTeacherCatalogError, match="closed schema|evaluation"):
        verify_derived_teacher_catalog_v1(output, root)


def test_catalog_verifier_fails_closed_when_rehashed_snapshot_index_binding_is_corrupted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mage_ptcg.meta_specialist.derived_teacher_catalog_v1 import (
        DerivedTeacherCatalogError,
        build_derived_teacher_catalog_v1,
        verify_derived_teacher_catalog_v1,
    )

    payload, output, root = _build_v2_catalog(tmp_path, monkeypatch)
    payload["teachers"][0]["collection"]["snapshot_index"]["file_sha256"] = "0" * 64
    payload["catalog_sha256"] = _catalog_sha(payload)
    output.write_bytes(_canonical(payload))

    with pytest.raises(DerivedTeacherCatalogError, match="snapshot index file SHA-256"):
        verify_derived_teacher_catalog_v1(output, root)
