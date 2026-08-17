from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest

from mage_ptcg.student.dataset import RuleBCExample, build_rule_bc_example, validate_example


PURPOSE = "DERIVED_MULTI_TEACHER_THETA0_PRETRAIN_ONLY"
SPLITS = ("train", "validation", "test")


def _observation() -> dict[str, object]:
    card = {
        "id": 1,
        "serial": 0,
        "playerIndex": 0,
        "hp": 100,
        "maxHp": 100,
        "appearThisTurn": False,
        "energies": [],
        "energyCards": [],
        "tools": [],
        "preEvolution": [],
    }
    player = {
        "active": [],
        "asleep": False,
        "bench": [],
        "benchMax": 5,
        "burned": False,
        "confused": False,
        "deckCount": 53,
        "discard": [],
        "hand": [card],
        "handCount": 1,
        "paralyzed": False,
        "poisoned": False,
        "prize": [object() for _ in range(6)],
    }
    return {
        "current": {
            "energyAttached": False,
            "firstPlayer": 0,
            "players": [player, player],
            "result": -1,
            "retreated": False,
            "stadium": [],
            "stadiumPlayed": False,
            "supporterPlayed": False,
            "turn": 2,
            "turnActionCount": 3,
            "yourIndex": 0,
        },
        "select": {
            "context": 0,
            "maxCount": 1,
            "minCount": 1,
            "option": [{"type": 14}, {"type": 13, "attackId": 1}],
            "type": 0,
        },
        "step": 7,
    }


def _example(*, episode: str, minimum: int, maximum: int, count: int) -> RuleBCExample:
    base = build_rule_bc_example(
        _observation(),
        deck=[1] * 60,
        source_id=episode,
        source_revision="synthetic-v3-set-test",
    )
    legal = tuple(sorted(action["digest"] for action in base.legal_actions))
    value = replace(
        base,
        example_id=hashlib.sha256(f"example:{episode}".encode()).hexdigest(),
        source_id=hashlib.sha256(f"episode:{episode}".encode()).hexdigest(),
        min_count=minimum,
        max_count=maximum,
        target_action_digests=legal[:count],
        teacher_ranking=tuple((digest, 0) for digest in legal),
    )
    validate_example(value)
    return value


def _row(*, split: str, episode: str, minimum: int, maximum: int, count: int) -> dict[str, object]:
    example = _example(episode=episode, minimum=minimum, maximum=maximum, count=count)
    source_record_sha = hashlib.sha256(episode.encode()).hexdigest()
    example = replace(
        example,
        source_revision="d" * 64,
        metadata={
            "bridge_schema": "meta-specialist-teacher-student-v3-set-bridge-v1",
            "source_record_sha256": source_record_sha,
        },
    )
    validate_example(example)
    return {
        "schema_version": "offline-scaleup-student-v3-set-source-v1",
        "purpose": PURPOSE,
        "record_id": example.example_id,
        "split": split,
        "episode_id": example.source_id,
        "near_duplicate_id": hashlib.sha256(f"near:{episode}".encode()).hexdigest(),
        "near_duplicate_ubiquitous": False,
        "candidate_outcome": "WIN" if count else "LOSS",
        "sample_weight": 1.0,
        "rule_bc_example": example.to_dict(),
        "provenance": {
            "catalog_sha256": "a" * 64,
            "snapshot_sha256": "b" * 64,
            "source_record_sha256": source_record_sha,
            "teacher_policy_sha256": example.source_revision,
            "teacher_deck_sha256": example.deck_fingerprint,
            "teacher_manifest_sha256": "e" * 64,
            "native_code_bundled": False,
            "native_deck_bundled": False,
        },
        "authority": {
            "training_authority": False,
            "promotion_authority": False,
            "submission_authority": False,
        },
    }


def _write_source(path: Path) -> list[dict[str, object]]:
    rows = [
        _row(split="train", episode="train-zero", minimum=0, maximum=2, count=0),
        _row(split="train", episode="train-one", minimum=0, maximum=2, count=1),
        _row(split="train", episode="train-two", minimum=1, maximum=2, count=2),
        _row(split="validation", episode="validation", minimum=0, maximum=2, count=0),
        _row(split="test", episode="test", minimum=1, maximum=1, count=1),
    ]
    path.write_text(
        "".join(
            json.dumps(
                row,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )
    return rows


def test_set_dataset_preserves_zero_variable_and_fixed_multi_targets(tmp_path: Path) -> None:
    from mage_ptcg.offline_scaleup.gpu_student_v3_set import (
        _examples,
        _load_split,
        build_set_dataset,
    )

    source = tmp_path / "source.jsonl"
    rows = _write_source(source)
    first = build_set_dataset(
        source=source, output_dir=tmp_path / "gpu", shard_size=2, synthetic_test_only=True
    )
    second = build_set_dataset(
        source=source, output_dir=tmp_path / "gpu", shard_size=2, synthetic_test_only=True
    )

    assert first == second
    assert first["schema_version"] == "offline-scaleup-gpu-set-dataset-v1"
    assert first["purpose"] == PURPOSE
    assert first["source_dataset_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert first["records"] == {
        "train": 3,
        "validation": 1,
        "test": 1,
    }
    assert first["episode_leakage"] == 0
    assert first["non_ubiquitous_near_duplicate_leakage"] == 0
    assert first["authority"] == {
        "training_authority": False,
        "promotion_authority": False,
        "submission_authority": False,
    }
    values = list(_examples(_load_split(tmp_path / "gpu", "train")))
    assert [value[3] for value in values] == [0, 1, 2]
    assert [int(value[2].sum().item()) for value in values] == [0, 1, 2]
    assert [(value[4], value[5]) for value in values] == [(0, 2), (0, 2), (1, 2)]
    assert [value[6]["record_id"] for value in values] == [
        rows[index]["record_id"] for index in range(3)
    ]
    assert sum(first["records"].values()) == len(rows)


def test_performance_dataset_requires_hash_bound_ready_bridge_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mage_ptcg.offline_scaleup.gpu_student_v3_set import (
        GPUStudentV3SetError,
        build_set_dataset,
    )

    source = tmp_path / "source.jsonl"
    rows = _write_source(source)
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    provenance = rows[0]["provenance"]
    assert isinstance(provenance, dict)
    bridge_source = {
        "teacher_id": "fixture",
        "archetype": "fixture",
        "policy_sha256": provenance["teacher_policy_sha256"],
        "deck_sha256": provenance["teacher_deck_sha256"],
        "source_kind": "fixture_teacher",
        "permission_manifest_id": "1" * 64,
        "permission_trusted_bytes_sha256": "2" * 64,
        "teacher_manifest_sha256": provenance["teacher_manifest_sha256"],
        "dataset_snapshot_sha256": "3" * 64,
        "snapshot_index_sha256": "4" * 64,
        "source_records": len(rows),
        "source_episodes": len(rows),
        "trainable_decisions": len(rows),
        "trainable_episodes": len(rows),
        "dataset_chunks": [
            {
                "position": 0,
                "sha256": "5" * 64,
                "manifest_id": "6" * 64,
                "manifest_content_hash": "7" * 64,
            }
        ],
        "snapshot_shards": [
            {"snapshot_id": "8" * 64, "sha256": "9" * 64, "examples": len(rows)}
        ],
        "sealed_split_audit": {
            "episode_split_intersection_count": 0,
            "non_ubiquitous_near_duplicate_split_intersection_count": 0,
        },
        "native_code_bundled": False,
        "native_deck_bundled": False,
    }
    bridge = {
        "schema_version": "meta-specialist-teacher-student-v3-set-bridge-v2",
        "purpose": PURPOSE,
        "catalog_path": "fixture/catalog.json",
        "catalog_file_sha256": "f" * 64,
        "catalog_sha256": "a" * 64,
        "decision_sha256": "d" * 64,
        "selected_teacher_ids": ["fixture"],
        "sources": [bridge_source],
        "trainer_contract": {},
        "feature_boundary": {},
        "compatibility": {"unsupported_total": 0},
        "split": {},
        "performance_training_ready": True,
        "blocked_reasons": [],
        "output_dataset": str(source.resolve()),
        "output_dataset_sha256": source_sha,
        "output_rows": len(rows),
        "partial_dataset_published": False,
        "authority": {
            "training_authority": False,
            "promotion_authority": False,
            "submission_authority": False,
            "teacher_code_submission_allowed": False,
            "teacher_deck_submission_allowed": False,
        },
        "bridge_sha256": None,
    }
    bridge["bridge_sha256"] = hashlib.sha256(
        b"meta-specialist-teacher-student-v3-set-bridge-v2\0"
        + json.dumps(
            {key: value for key, value in bridge.items() if key != "bridge_sha256"},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    bridge_path = tmp_path / "bridge.json"
    bridge_path.write_text(
        json.dumps(bridge, sort_keys=True, separators=(",", ":")), encoding="utf-8"
    )
    import mage_ptcg.meta_specialist.teacher_snapshot_student_v3_bridge_v1 as bridge_module

    monkeypatch.setattr(
        bridge_module,
        "verify_teacher_snapshot_student_v3_bridge_manifest_v1",
        lambda path, _root: json.loads(Path(path).read_text(encoding="utf-8")),
    )

    with pytest.raises(GPUStudentV3SetError, match="bridge manifest is required"):
        build_set_dataset(source=source, output_dir=tmp_path / "missing")
    manifest = build_set_dataset(
        source=source,
        output_dir=tmp_path / "gpu",
        bridge_manifest=bridge_path,
    )
    assert manifest["bridge_manifest_sha256"] == hashlib.sha256(
        bridge_path.read_bytes()
    ).hexdigest()
    assert manifest["bridge_sha256"] == bridge["bridge_sha256"]
    assert manifest["selected_teacher_ids"] == ["fixture"]

    tampered = dict(bridge)
    tampered["trainer_contract"] = {"silently_changed": True}
    tampered_path = tmp_path / "tampered.json"
    tampered_path.write_text(
        json.dumps(tampered, sort_keys=True, separators=(",", ":")), encoding="utf-8"
    )
    with pytest.raises(GPUStudentV3SetError, match="semantic SHA-256 mismatch"):
        build_set_dataset(
            source=source,
            output_dir=tmp_path / "tampered-output",
            bridge_manifest=tampered_path,
        )

    bridge["performance_training_ready"] = False
    bridge["blocked_reasons"] = ["unsafe"]
    blocked_path = tmp_path / "blocked.json"
    blocked_path.write_text(
        json.dumps(bridge, sort_keys=True, separators=(",", ":")), encoding="utf-8"
    )
    with pytest.raises(GPUStudentV3SetError, match="not performance-training ready"):
        build_set_dataset(
            source=source,
            output_dir=tmp_path / "blocked-output",
            bridge_manifest=blocked_path,
        )


def test_set_dataset_rejects_ordered_selection_without_publishing_manifest(tmp_path: Path) -> None:
    from mage_ptcg.offline_scaleup.gpu_student_v3_set import (
        GPUStudentV3SetError,
        build_set_dataset,
    )

    source = tmp_path / "ordered.jsonl"
    rows = _write_source(source)
    example = dict(rows[0]["rule_bc_example"])
    example["selection_type"] = 5
    example["selection_context"] = 34
    rows[0] = {**rows[0], "rule_bc_example": example}
    source.write_text(
        "".join(
            json.dumps(
                row,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )

    with pytest.raises(GPUStudentV3SetError, match="ordered"):
        build_set_dataset(
            source=source, output_dir=tmp_path / "gpu", shard_size=2, synthetic_test_only=True
        )
    assert not (tmp_path / "gpu" / "manifest.json").exists()


def test_set_dataset_rejects_extra_rule_example_fields(tmp_path: Path) -> None:
    from mage_ptcg.offline_scaleup.gpu_student_v3_set import (
        GPUStudentV3SetError,
        build_set_dataset,
    )

    source = tmp_path / "extra.jsonl"
    rows = _write_source(source)
    rows[0]["rule_bc_example"]["opponent_private_hand"] = [999]
    source.write_text(
        "".join(
            json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )
    with pytest.raises(GPUStudentV3SetError, match="RuleBCExample.*closed schema"):
        build_set_dataset(
            source=source, output_dir=tmp_path / "gpu", shard_size=2, synthetic_test_only=True
        )


def test_shard_verifier_rejects_fractional_or_wrong_dtype_tensors(tmp_path: Path) -> None:
    import torch

    from mage_ptcg.offline_scaleup.gpu_student_v3_set import (
        GPUStudentV3SetError,
        _verify_shard_payload,
        build_set_dataset,
    )

    source = tmp_path / "source.jsonl"
    _write_source(source)
    dataset_dir = tmp_path / "gpu"
    manifest = build_set_dataset(
        source=source, output_dir=dataset_dir, shard_size=2, synthetic_test_only=True
    )
    shard_path = dataset_dir / manifest["shards"][0]["path"]
    payload = torch.load(shard_path, map_location="cpu", weights_only=False)

    wrong_float = dict(payload)
    wrong_float["state"] = payload["state"].double()
    with pytest.raises(GPUStudentV3SetError, match="dtype"):
        _verify_shard_payload(wrong_float)

    fractional = dict(payload)
    fractional["offsets"] = payload["offsets"].float()
    fractional["target_count"] = payload["target_count"].float() + 0.9
    fractional["min_count"] = payload["min_count"].float() + 0.9
    fractional["max_count"] = payload["max_count"].float() + 0.9
    with pytest.raises(GPUStudentV3SetError, match="dtype"):
        _verify_shard_payload(fractional)

    nonfinite = dict(payload)
    nonfinite["actions"] = payload["actions"].clone()
    nonfinite["actions"][0, 0] = float("nan")
    with pytest.raises(GPUStudentV3SetError, match="finite"):
        _verify_shard_payload(nonfinite)


def test_dataset_manifest_rejects_duplicate_relabelled_and_escaping_shards(
    tmp_path: Path,
) -> None:
    from mage_ptcg.offline_scaleup.gpu_student_v3_set import (
        GPUStudentV3SetError,
        _load_split,
        _semantic_sha,
        build_set_dataset,
    )

    source = tmp_path / "source.jsonl"
    _write_source(source)
    dataset_dir = tmp_path / "gpu"
    build_set_dataset(
        source=source, output_dir=dataset_dir, shard_size=2, synthetic_test_only=True
    )
    manifest_path = dataset_dir / "manifest.json"
    original = json.loads(manifest_path.read_text(encoding="utf-8"))

    duplicate = json.loads(json.dumps(original))
    duplicate["shards"].append(dict(duplicate["shards"][0]))
    duplicate["dataset_sha256"] = _semantic_sha(
        {key: value for key, value in duplicate.items() if key != "dataset_sha256"},
        domain="offline-scaleup-gpu-set-dataset-v1",
    )
    manifest_path.write_text(
        json.dumps(duplicate, sort_keys=True, separators=(",", ":")), encoding="utf-8"
    )
    with pytest.raises(GPUStudentV3SetError, match="unique"):
        _load_split(dataset_dir, "train")

    relabelled = json.loads(json.dumps(original))
    validation = next(row for row in relabelled["shards"] if row["split"] == "validation")
    validation["split"] = "train"
    relabelled["dataset_sha256"] = _semantic_sha(
        {key: value for key, value in relabelled.items() if key != "dataset_sha256"},
        domain="offline-scaleup-gpu-set-dataset-v1",
    )
    manifest_path.write_text(
        json.dumps(relabelled, sort_keys=True, separators=(",", ":")), encoding="utf-8"
    )
    with pytest.raises(GPUStudentV3SetError, match="metadata split"):
        _load_split(dataset_dir, "train")

    escaping = json.loads(json.dumps(original))
    escaping["shards"][0]["path"] = "../outside.pt"
    escaping["dataset_sha256"] = _semantic_sha(
        {key: value for key, value in escaping.items() if key != "dataset_sha256"},
        domain="offline-scaleup-gpu-set-dataset-v1",
    )
    manifest_path.write_text(
        json.dumps(escaping, sort_keys=True, separators=(",", ":")), encoding="utf-8"
    )
    with pytest.raises(GPUStudentV3SetError, match="direct child"):
        _load_split(dataset_dir, "train")


def test_training_rejects_a_forged_performance_provenance_manifest(tmp_path: Path) -> None:
    from mage_ptcg.offline_scaleup.gpu_student_v3_set import (
        GPUStudentV3SetError,
        _semantic_sha,
        build_set_dataset,
        train_set_student,
    )

    source = tmp_path / "source.jsonl"
    _write_source(source)
    dataset_dir = tmp_path / "gpu"
    build_set_dataset(
        source=source, output_dir=dataset_dir, shard_size=2, synthetic_test_only=True
    )
    manifest_path = dataset_dir / "manifest.json"
    forged = json.loads(manifest_path.read_text(encoding="utf-8"))
    forged.update(
        {
            "synthetic_test_only": False,
            "bridge_manifest_path": str(tmp_path / "missing-bridge.json"),
            "bridge_manifest_sha256": "b" * 64,
            "bridge_sha256": "c" * 64,
            "selected_teacher_ids": ["forged"],
        }
    )
    forged["dataset_sha256"] = _semantic_sha(
        {key: value for key, value in forged.items() if key != "dataset_sha256"},
        domain="offline-scaleup-gpu-set-dataset-v1",
    )
    manifest_path.write_text(
        json.dumps(forged, sort_keys=True, separators=(",", ":")), encoding="utf-8"
    )
    with pytest.raises(GPUStudentV3SetError, match="performance provenance"):
        train_set_student(
            dataset_dir=dataset_dir,
            output_dir=tmp_path / "model",
            device_name="cpu",
            epochs=1,
            batch_size=3,
            hidden=8,
            blocks=0,
        )

def test_set_model_and_loss_are_permutation_invariant_and_mask_illegal_counts() -> None:
    import torch

    from mage_ptcg.offline_scaleup.gpu_student_v3_set import (
        make_set_cardinality_model,
        set_cardinality_loss,
    )

    torch.manual_seed(13)
    model = make_set_cardinality_model(hidden=16, blocks=1, dropout=0.0, max_count=2)
    model.eval()
    state = torch.randn(2, 32)
    actions = torch.randn(2, 3, 64)
    legal = torch.tensor([[True, True, False], [True, True, True]])
    permutation = torch.tensor([2, 0, 1])

    action_logits, count_logits = model(state, actions, legal)
    permuted_action, permuted_count = model(
        state,
        actions[:, permutation],
        legal[:, permutation],
    )
    assert torch.allclose(action_logits[:, permutation], permuted_action, atol=1e-6)
    assert torch.allclose(count_logits, permuted_count, atol=1e-6)

    batch = {
        "legal_mask": legal,
        "target_set": torch.tensor([[False, False, False], [True, False, True]]),
        "target_count": torch.tensor([0, 2]),
        "min_count": torch.tensor([0, 1]),
        "max_count": torch.tensor([2, 2]),
    }
    first = set_cardinality_loss(action_logits, count_logits, batch)
    changed_actions = action_logits.clone()
    changed_actions[0, 2] = 1_000_000.0
    changed_counts = count_logits.clone()
    changed_counts[1, 0] = 1_000_000.0
    second = set_cardinality_loss(changed_actions, changed_counts, batch)
    for key in ("total", "set", "count"):
        assert torch.isfinite(first[key])
        assert torch.allclose(first[key], second[key], atol=1e-6)


def test_set_loss_rejects_target_outside_legal_mask() -> None:
    import torch

    from mage_ptcg.offline_scaleup.gpu_student_v3_set import (
        GPUStudentV3SetError,
        set_cardinality_loss,
    )

    batch = {
        "legal_mask": torch.tensor([[True, False]]),
        "target_set": torch.tensor([[False, True]]),
        "target_count": torch.tensor([1]),
        "min_count": torch.tensor([0]),
        "max_count": torch.tensor([1]),
    }
    with pytest.raises(GPUStudentV3SetError, match="non-legal"):
        set_cardinality_loss(torch.zeros(1, 2), torch.zeros(1, 2), batch)


def test_decode_ties_follow_stable_action_digest_under_permutation() -> None:
    import torch

    from mage_ptcg.offline_scaleup.gpu_student_v3_set import decode_set_predictions

    digests = ["c" * 64, "a" * 64, "b" * 64]
    logits = torch.zeros(1, 3)
    counts = torch.tensor([[0.0, 1.0]])
    legal = torch.ones(1, 3, dtype=torch.bool)
    minimum = torch.tensor([1])
    maximum = torch.tensor([1])
    original = decode_set_predictions(
        logits, counts, legal, minimum, maximum, [digests]
    )
    permutation = [2, 0, 1]
    permuted_digests = [digests[index] for index in permutation]
    permuted = decode_set_predictions(
        logits[:, permutation],
        counts,
        legal[:, permutation],
        minimum,
        maximum,
        [permuted_digests],
    )
    assert digests[original[0][0]] == "a" * 64
    assert permuted_digests[permuted[0][0]] == "a" * 64


def _sidecar_payload(
    *, dataset_manifest_sha256: str, catalog_sha256: str, record_ids: list[str]
) -> dict[str, object]:
    return {
        "schema_version": "offline-scaleup-student-v3-weight-sidecar-v1",
        "objective_kind": "AWR_FINE_TUNE",
        "dataset_manifest_sha256": dataset_manifest_sha256,
        "catalog_sha256": catalog_sha256,
        "weights": [
            {"record_id": record_id, "weight": float(index + 1)}
            for index, record_id in enumerate(record_ids)
        ],
        "authority": {
            "training_authority": False,
            "promotion_authority": False,
            "submission_authority": False,
        },
    }


def test_weight_sidecar_strictly_joins_every_and_only_train_record(tmp_path: Path) -> None:
    from mage_ptcg.offline_scaleup.gpu_student_v3_set import (
        GPUStudentV3SetError,
        _examples,
        _load_split,
        build_set_dataset,
        load_training_weight_sidecar,
        train_set_student,
    )

    source = tmp_path / "source.jsonl"
    _write_source(source)
    dataset_dir = tmp_path / "gpu"
    manifest = build_set_dataset(
        source=source, output_dir=dataset_dir, shard_size=2, synthetic_test_only=True
    )
    manifest_sha = hashlib.sha256((dataset_dir / "manifest.json").read_bytes()).hexdigest()
    train_ids = [value[6]["record_id"] for value in _examples(_load_split(dataset_dir, "train"))]
    sidecar = tmp_path / "weights.json"
    payload = _sidecar_payload(
        dataset_manifest_sha256=manifest_sha,
        catalog_sha256=manifest["catalog_sha256"],
        record_ids=train_ids,
    )
    sidecar.write_text(
        json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    joined, stats = load_training_weight_sidecar(
        sidecar,
        dataset_manifest_sha256=manifest_sha,
        catalog_sha256=manifest["catalog_sha256"],
        train_record_ids=train_ids,
    )
    assert set(joined) == set(train_ids)
    assert stats["external_weight_mass"] == 6.0
    assert stats["external_weight_ess"] == pytest.approx(36.0 / 14.0)
    summary = train_set_student(
        dataset_dir=dataset_dir,
        output_dir=tmp_path / "awr-model",
        device_name="cpu",
        epochs=1,
        batch_size=3,
        hidden=8,
        blocks=0,
        dropout=0.0,
        learning_rate=0.01,
        seed=19,
        weight_sidecar=sidecar,
    )
    assert summary["objective_kind"] == "AWR_FINE_TUNE"
    assert summary["catalog_sha256"] == manifest["catalog_sha256"]
    assert summary["dataset_manifest_sha256"] == manifest_sha
    assert summary["weight_sidecar_sha256"] == hashlib.sha256(sidecar.read_bytes()).hexdigest()
    assert summary["effective_weight_mass"] == 6.0
    assert summary["effective_weight_ess"] == pytest.approx(36.0 / 14.0)

    invalid_payloads = []
    missing = json.loads(json.dumps(payload))
    missing["weights"] = missing["weights"][:-1]
    invalid_payloads.append((missing, "missing"))
    duplicate = json.loads(json.dumps(payload))
    duplicate["weights"].append(dict(duplicate["weights"][0]))
    invalid_payloads.append((duplicate, "duplicated"))
    extra = json.loads(json.dumps(payload))
    extra["weights"].append({"record_id": "f" * 64, "weight": 1.0})
    invalid_payloads.append((extra, "extra"))
    nonpositive = json.loads(json.dumps(payload))
    nonpositive["weights"][0]["weight"] = 0.0
    invalid_payloads.append((nonpositive, "positive"))
    for index, (invalid, message) in enumerate(invalid_payloads):
        path = tmp_path / f"invalid-{index}.json"
        path.write_text(
            json.dumps(invalid, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        with pytest.raises(GPUStudentV3SetError, match=message):
            load_training_weight_sidecar(
                path,
                dataset_manifest_sha256=manifest_sha,
                catalog_sha256=manifest["catalog_sha256"],
                train_record_ids=train_ids,
            )


def test_awr_can_start_from_a_strict_theta0_best_checkpoint(tmp_path: Path) -> None:
    from mage_ptcg.offline_scaleup.gpu_student_v3_set import (
        GPUStudentV3SetError,
        _examples,
        _load_split,
        build_set_dataset,
        train_set_student,
    )

    source = tmp_path / "source.jsonl"
    _write_source(source)
    dataset_dir = tmp_path / "gpu"
    manifest = build_set_dataset(
        source=source, output_dir=dataset_dir, shard_size=2, synthetic_test_only=True
    )
    manifest_sha = hashlib.sha256((dataset_dir / "manifest.json").read_bytes()).hexdigest()
    train_ids = [value[6]["record_id"] for value in _examples(_load_split(dataset_dir, "train"))]
    sidecar = tmp_path / "weights.json"
    sidecar.write_text(
        json.dumps(
            _sidecar_payload(
                dataset_manifest_sha256=manifest_sha,
                catalog_sha256=manifest["catalog_sha256"],
                record_ids=train_ids,
            ),
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    theta_dir = tmp_path / "theta0"
    theta = train_set_student(
        dataset_dir=dataset_dir,
        output_dir=theta_dir,
        device_name="cpu",
        epochs=1,
        batch_size=3,
        hidden=8,
        blocks=0,
        seed=83,
    )
    awr = train_set_student(
        dataset_dir=dataset_dir,
        output_dir=tmp_path / "awr",
        device_name="cpu",
        epochs=1,
        batch_size=3,
        hidden=8,
        blocks=0,
        seed=89,
        weight_sidecar=sidecar,
        initial_model_dir=theta_dir,
    )
    assert awr["objective_kind"] == "AWR_FINE_TUNE"
    assert awr["training_config"]["initialization_kind"] == "THETA0_BEST_CHECKPOINT"
    assert awr["training_config"]["initial_checkpoint_sha256"] == theta[
        "best_checkpoint_sha256"
    ]
    assert awr["training_config"]["initial_training_summary_sha256"] == hashlib.sha256(
        (theta_dir / "training_summary.json").read_bytes()
    ).hexdigest()

    with pytest.raises(GPUStudentV3SetError, match="initial model config"):
        train_set_student(
            dataset_dir=dataset_dir,
            output_dir=tmp_path / "bad-awr",
            device_name="cpu",
            epochs=1,
            batch_size=3,
            hidden=12,
            blocks=0,
            seed=89,
            weight_sidecar=sidecar,
            initial_model_dir=theta_dir,
        )


def test_weighted_empirical_batch_loss_uses_global_mean_and_is_partition_invariant() -> None:
    import torch

    from mage_ptcg.offline_scaleup.gpu_student_v3_set import (
        weighted_empirical_batch_loss_v1,
    )

    weights = torch.tensor([1.0, 10.0, 100.0])
    global_mean = float(weights.mean().item())

    full_losses = torch.tensor([1.0, 3.0, 9.0], requires_grad=True)
    full = weighted_empirical_batch_loss_v1(
        full_losses,
        weights,
        global_effective_weight_mean=global_mean,
    )
    full.backward()
    full_gradient = full_losses.grad.detach().clone()

    partitioned_losses = torch.tensor([1.0, 3.0, 9.0], requires_grad=True)
    first = weighted_empirical_batch_loss_v1(
        partitioned_losses[:1],
        weights[:1],
        global_effective_weight_mean=global_mean,
    )
    second = weighted_empirical_batch_loss_v1(
        partitioned_losses[1:],
        weights[1:],
        global_effective_weight_mean=global_mean,
    )
    combined = (first * 1.0 + second * 2.0) / 3.0
    combined.backward()
    assert torch.allclose(combined, full)
    assert torch.allclose(partitioned_losses.grad, full_gradient)

    unit_loss = torch.tensor([2.0], requires_grad=True)
    light = weighted_empirical_batch_loss_v1(
        unit_loss,
        torch.tensor([1.0]),
        global_effective_weight_mean=global_mean,
    )
    light.backward()
    light_gradient = float(unit_loss.grad.item())
    unit_loss.grad.zero_()
    heavy = weighted_empirical_batch_loss_v1(
        unit_loss,
        torch.tensor([10.0]),
        global_effective_weight_mean=global_mean,
    )
    heavy.backward()
    assert float(unit_loss.grad.item()) == pytest.approx(10.0 * light_gradient)


def test_awr_training_does_not_apply_nonlinear_gradient_clipping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import torch

    from mage_ptcg.offline_scaleup.gpu_student_v3_set import (
        _examples,
        _load_split,
        build_set_dataset,
        train_set_student,
    )

    source = tmp_path / "source.jsonl"
    _write_source(source)
    dataset_dir = tmp_path / "gpu"
    manifest = build_set_dataset(
        source=source, output_dir=dataset_dir, shard_size=2, synthetic_test_only=True
    )
    manifest_sha = hashlib.sha256((dataset_dir / "manifest.json").read_bytes()).hexdigest()
    train_ids = [value[6]["record_id"] for value in _examples(_load_split(dataset_dir, "train"))]
    sidecar = tmp_path / "weights.json"
    sidecar.write_text(
        json.dumps(
            _sidecar_payload(
                dataset_manifest_sha256=manifest_sha,
                catalog_sha256=manifest["catalog_sha256"],
                record_ids=train_ids,
            ),
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )

    def forbidden_clip(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("AWR gradient clipping changes the weighted objective")

    monkeypatch.setattr(torch.nn.utils, "clip_grad_norm_", forbidden_clip)
    train_set_student(
        dataset_dir=dataset_dir,
        output_dir=tmp_path / "model",
        device_name="cpu",
        epochs=1,
        batch_size=1,
        hidden=8,
        blocks=0,
        learning_rate=0.001,
        seed=23,
        weight_sidecar=sidecar,
    )


def test_train_checkpoint_resume_and_strict_config_sha(tmp_path: Path) -> None:
    import torch

    from mage_ptcg.offline_scaleup.gpu_student_v3_set import (
        GPUStudentV3SetError,
        build_set_dataset,
        evaluate_set_student,
        load_set_checkpoint,
        train_set_student,
    )

    source = tmp_path / "source.jsonl"
    _write_source(source)
    dataset_dir = tmp_path / "gpu"
    build_set_dataset(
        source=source, output_dir=dataset_dir, shard_size=2, synthetic_test_only=True
    )
    model_dir = tmp_path / "model"
    first = train_set_student(
        dataset_dir=dataset_dir,
        output_dir=model_dir,
        device_name="cpu",
        epochs=2,
        batch_size=3,
        hidden=16,
        blocks=1,
        dropout=0.0,
        learning_rate=0.02,
        seed=47,
    )
    assert first["objective_kind"] == "THETA0_PRETRAIN"
    assert first["weight_sidecar_sha256"] is None
    assert first["authority"] == {
        "training_authority": False,
        "promotion_authority": False,
        "submission_authority": False,
    }
    assert first["epochs_completed"] == 2
    _model, loaded = load_set_checkpoint(model_dir, torch.device("cpu"))
    assert loaded["best_checkpoint_sha256"] == first["best_checkpoint_sha256"]
    evaluation = evaluate_set_student(
        dataset_dir=dataset_dir,
        model_dir=model_dir,
        output=tmp_path / "evaluation.json",
        device_name="cpu",
        batch_size=2,
    )
    assert set(evaluation["splits"]) == set(SPLITS)
    assert evaluation["gpu_cpu_decode_parity"] == "cpu_only"
    assert evaluation["authority"]["promotion_authority"] is False

    resumed = train_set_student(
        dataset_dir=dataset_dir,
        output_dir=model_dir,
        device_name="cpu",
        epochs=3,
        batch_size=3,
        hidden=16,
        blocks=1,
        dropout=0.0,
        learning_rate=0.02,
        seed=47,
        resume=True,
    )
    assert resumed["resumed_from_checkpoint"] is True
    assert resumed["epochs_completed"] == 3
    with pytest.raises(GPUStudentV3SetError, match="model_config_sha256"):
        train_set_student(
            dataset_dir=dataset_dir,
            output_dir=model_dir,
            device_name="cpu",
            epochs=4,
            batch_size=3,
            hidden=20,
            blocks=1,
            dropout=0.0,
            learning_rate=0.02,
            seed=47,
            resume=True,
        )

    summary_path = model_dir / "training_summary.json"
    tampered = json.loads(summary_path.read_text(encoding="utf-8"))
    tampered["best_checkpoint_sha256"] = "0" * 64
    summary_path.write_text(
        json.dumps(tampered, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    with pytest.raises(GPUStudentV3SetError, match="best checkpoint SHA-256"):
        load_set_checkpoint(model_dir, torch.device("cpu"))


def test_checkpoint_loader_rejects_training_config_tampering(tmp_path: Path) -> None:
    import torch

    from mage_ptcg.offline_scaleup.gpu_student_v3_set import (
        GPUStudentV3SetError,
        build_set_dataset,
        load_set_checkpoint,
        train_set_student,
    )

    source = tmp_path / "source.jsonl"
    _write_source(source)
    dataset_dir = tmp_path / "gpu"
    build_set_dataset(
        source=source, output_dir=dataset_dir, shard_size=2, synthetic_test_only=True
    )
    model_dir = tmp_path / "model"
    train_set_student(
        dataset_dir=dataset_dir,
        output_dir=model_dir,
        device_name="cpu",
        epochs=1,
        batch_size=3,
        hidden=8,
        blocks=0,
        seed=71,
    )
    summary_path = model_dir / "training_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["training_config"]["count_loss_weight"] = 999999.0
    summary_path.write_text(
        json.dumps(summary, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    with pytest.raises(GPUStudentV3SetError, match="training config SHA-256"):
        load_set_checkpoint(model_dir, torch.device("cpu"))


def test_checkpoint_loader_rejects_open_or_duplicate_summary_schema(
    tmp_path: Path,
) -> None:
    import torch

    from mage_ptcg.offline_scaleup.gpu_student_v3_set import (
        GPUStudentV3SetError,
        build_set_dataset,
        load_set_checkpoint,
        train_set_student,
    )

    source = tmp_path / "source.jsonl"
    _write_source(source)
    dataset_dir = tmp_path / "gpu"
    build_set_dataset(
        source=source, output_dir=dataset_dir, shard_size=2, synthetic_test_only=True
    )
    model_dir = tmp_path / "model"
    train_set_student(
        dataset_dir=dataset_dir,
        output_dir=model_dir,
        device_name="cpu",
        epochs=1,
        batch_size=3,
        hidden=8,
        blocks=0,
        seed=73,
    )
    summary_path = model_dir / "training_summary.json"
    original = json.loads(summary_path.read_text(encoding="utf-8"))

    opened = dict(original)
    opened["unverified_runtime_override"] = True
    summary_path.write_text(
        json.dumps(opened, sort_keys=True, separators=(",", ":")), encoding="utf-8"
    )
    with pytest.raises(GPUStudentV3SetError, match="closed schema"):
        load_set_checkpoint(model_dir, torch.device("cpu"))

    canonical = json.dumps(original, sort_keys=True, separators=(",", ":"))
    duplicate = canonical[:-1] + ',"purpose":"FORGED"}'
    summary_path.write_text(duplicate, encoding="utf-8")
    with pytest.raises(GPUStudentV3SetError, match="duplicate key"):
        load_set_checkpoint(model_dir, torch.device("cpu"))


def test_resume_recovers_a_fully_written_epoch_after_summary_interruption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import mage_ptcg.offline_scaleup.gpu_student_v3_set as module

    source = tmp_path / "source.jsonl"
    _write_source(source)
    dataset_dir = tmp_path / "gpu"
    module.build_set_dataset(
        source=source, output_dir=dataset_dir, shard_size=2, synthetic_test_only=True
    )
    model_dir = tmp_path / "model"
    original_atomic_json = module._atomic_json
    calls = 0

    def interrupt_first_summary(path: Path, value: object) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("injected after checkpoint commit")
        original_atomic_json(path, value)

    monkeypatch.setattr(module, "_atomic_json", interrupt_first_summary)
    with pytest.raises(RuntimeError, match="injected"):
        module.train_set_student(
            dataset_dir=dataset_dir,
            output_dir=model_dir,
            device_name="cpu",
            epochs=1,
            batch_size=3,
            hidden=16,
            blocks=1,
            dropout=0.25,
            learning_rate=0.01,
            seed=79,
        )
    assert list((model_dir / "checkpoints").glob("epoch-*.pt"))
    monkeypatch.setattr(module, "_atomic_json", original_atomic_json)
    resumed = module.train_set_student(
        dataset_dir=dataset_dir,
        output_dir=model_dir,
        device_name="cpu",
        epochs=2,
        batch_size=3,
        hidden=16,
        blocks=1,
        dropout=0.25,
        learning_rate=0.01,
        seed=79,
        resume=True,
    )
    assert resumed["epochs_completed"] == 2
    assert resumed["recovered_interrupted_epoch"] is True


def test_resume_rolls_forward_one_complete_epoch_past_a_stale_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import torch
    import mage_ptcg.offline_scaleup.gpu_student_v3_set as module

    source = tmp_path / "source.jsonl"
    _write_source(source)
    dataset_dir = tmp_path / "gpu"
    module.build_set_dataset(
        source=source, output_dir=dataset_dir, shard_size=2, synthetic_test_only=True
    )
    common = dict(
        dataset_dir=dataset_dir,
        device_name="cpu",
        batch_size=3,
        hidden=16,
        blocks=1,
        dropout=0.25,
        learning_rate=0.01,
        seed=81,
    )
    expected_dir = tmp_path / "expected"
    interrupted_dir = tmp_path / "interrupted"
    module.train_set_student(output_dir=expected_dir, epochs=3, **common)
    module.train_set_student(output_dir=interrupted_dir, epochs=1, **common)
    original_atomic_json = module._atomic_json

    def interrupt_summary(_path: Path, _value: object) -> None:
        raise RuntimeError("injected after second checkpoint")

    monkeypatch.setattr(module, "_atomic_json", interrupt_summary)
    with pytest.raises(RuntimeError, match="injected"):
        module.train_set_student(
            output_dir=interrupted_dir, epochs=2, resume=True, **common
        )
    monkeypatch.setattr(module, "_atomic_json", original_atomic_json)
    resumed = module.train_set_student(
        output_dir=interrupted_dir, epochs=3, resume=True, **common
    )
    assert resumed["recovered_interrupted_epoch"] is True
    expected = torch.load(expected_dir / "last.pt", map_location="cpu", weights_only=True)
    actual = torch.load(interrupted_dir / "last.pt", map_location="cpu", weights_only=True)
    assert all(torch.equal(expected["model"][key], actual["model"][key]) for key in expected["model"])


def test_dropout_resume_is_bit_exact_with_uninterrupted_training(tmp_path: Path) -> None:
    import torch

    from mage_ptcg.offline_scaleup.gpu_student_v3_set import (
        build_set_dataset,
        train_set_student,
    )

    source = tmp_path / "source.jsonl"
    _write_source(source)
    dataset_dir = tmp_path / "gpu"
    build_set_dataset(
        source=source, output_dir=dataset_dir, shard_size=2, synthetic_test_only=True
    )
    common = dict(
        dataset_dir=dataset_dir,
        device_name="cpu",
        batch_size=3,
        hidden=16,
        blocks=1,
        dropout=0.25,
        learning_rate=0.01,
        seed=83,
    )
    continuous_dir = tmp_path / "continuous"
    resumed_dir = tmp_path / "resumed"
    train_set_student(output_dir=continuous_dir, epochs=3, **common)
    train_set_student(output_dir=resumed_dir, epochs=1, **common)
    train_set_student(output_dir=resumed_dir, epochs=3, resume=True, **common)
    continuous = torch.load(
        continuous_dir / "last.pt", map_location="cpu", weights_only=True
    )["model"]
    resumed = torch.load(
        resumed_dir / "last.pt", map_location="cpu", weights_only=True
    )["model"]
    assert continuous.keys() == resumed.keys()
    assert all(torch.equal(continuous[key], resumed[key]) for key in continuous)


def test_resume_rejects_corrupt_committed_best_checkpoint(tmp_path: Path) -> None:
    from mage_ptcg.offline_scaleup.gpu_student_v3_set import (
        GPUStudentV3SetError,
        build_set_dataset,
        train_set_student,
    )

    source = tmp_path / "source.jsonl"
    _write_source(source)
    dataset_dir = tmp_path / "gpu"
    build_set_dataset(
        source=source, output_dir=dataset_dir, shard_size=2, synthetic_test_only=True
    )
    model_dir = tmp_path / "model"
    train_set_student(
        dataset_dir=dataset_dir,
        output_dir=model_dir,
        device_name="cpu",
        epochs=1,
        batch_size=3,
        hidden=16,
        blocks=1,
        dropout=0.0,
        learning_rate=0.01,
        seed=89,
    )
    best = model_dir / "best.pt"
    raw = bytearray(best.read_bytes())
    raw[len(raw) // 2] ^= 1
    best.write_bytes(raw)
    with pytest.raises(GPUStudentV3SetError, match="best checkpoint SHA-256"):
        train_set_student(
            dataset_dir=dataset_dir,
            output_dir=model_dir,
            device_name="cpu",
            epochs=2,
            batch_size=3,
            hidden=16,
            blocks=1,
            dropout=0.0,
            learning_rate=0.01,
            seed=89,
            resume=True,
        )


def test_gpu_bf16_tiny_overfit_and_cpu_decode_parity() -> None:
    import torch

    if not torch.cuda.is_available():
        pytest.skip("CUDA unavailable: GPU tiny-overfit/CPU parity not executable")

    from mage_ptcg.offline_scaleup.gpu_student_v3_set import (
        decode_set_predictions,
        make_set_cardinality_model,
        set_cardinality_loss,
    )

    torch.manual_seed(91)
    device = torch.device("cuda")
    state = torch.randn(6, 32, device=device)
    actions = torch.randn(6, 4, 64, device=device)
    legal = torch.ones(6, 4, dtype=torch.bool, device=device)
    target_set = torch.tensor(
        [
            [False, False, False, False],
            [True, False, False, False],
            [False, True, True, False],
            [False, False, False, True],
            [True, False, True, False],
            [False, True, False, False],
        ],
        device=device,
    )
    batch = {
        "legal_mask": legal,
        "target_set": target_set,
        "target_count": target_set.sum(dim=1).long(),
        "min_count": torch.tensor([0, 0, 1, 1, 2, 0], device=device),
        "max_count": torch.tensor([2, 2, 2, 1, 2, 2], device=device),
    }
    model = make_set_cardinality_model(hidden=32, blocks=1, dropout=0.0, max_count=2).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=0.02)

    with torch.no_grad(), torch.autocast(device_type="cuda", dtype=torch.bfloat16):
        initial_logits, initial_counts = model(state, actions, legal)
        initial = float(
            set_cardinality_loss(initial_logits, initial_counts, batch)["total"].item()
        )
    for _step in range(80):
        optimizer.zero_grad(set_to_none=True)
        with torch.autocast(device_type="cuda", dtype=torch.bfloat16):
            action_logits, count_logits = model(state, actions, legal)
            loss = set_cardinality_loss(action_logits, count_logits, batch)["total"]
        loss.backward()
        optimizer.step()
    with torch.no_grad():
        gpu_actions, gpu_counts = model(state, actions, legal)
        final = float(set_cardinality_loss(gpu_actions, gpu_counts, batch)["total"].item())
        gpu_decode = decode_set_predictions(
            gpu_actions,
            gpu_counts,
            legal,
            batch["min_count"],
            batch["max_count"],
            [[hashlib.sha256(f"{row}:{column}".encode()).hexdigest() for column in range(4)] for row in range(6)],
        )
    assert final < initial * 0.2

    cpu_model = make_set_cardinality_model(hidden=32, blocks=1, dropout=0.0, max_count=2)
    cpu_model.load_state_dict(model.state_dict())
    cpu_model.eval()
    with torch.no_grad():
        cpu_actions, cpu_counts = cpu_model(state.cpu(), actions.cpu(), legal.cpu())
        cpu_decode = decode_set_predictions(
            cpu_actions,
            cpu_counts,
            legal.cpu(),
            batch["min_count"].cpu(),
            batch["max_count"].cpu(),
            [[hashlib.sha256(f"{row}:{column}".encode()).hexdigest() for column in range(4)] for row in range(6)],
        )
    assert cpu_decode == gpu_decode
