"""Focused contracts for the Offline Training v1 pipeline.

These tests exercise workspace-independent paths, config validation, the run
lifecycle (atomic manifest, locking, resume), the sharded dataset with an
episode-level split, the neural Student (finite training, checkpoint resume,
incompatibility rejection, OOM reduction), PyTorch/export parity, the runtime
fallback, and the separate Kaggle package with clean-room extraction.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tarfile

import pytest

from mage_ptcg.offline_training import runstate
from mage_ptcg.offline_training.collection import collection_dataset_path, run_collection
from mage_ptcg.offline_training.config import ConfigError, load_config
from mage_ptcg.offline_training.dataset import (
    OfflineDatasetError,
    build_dataset,
    deterministic_episode_split,
    iter_decisions,
    load_manifest,
    verify_shards,
)
from mage_ptcg.offline_training.environment import doctor, resolve_resource_policy
from mage_ptcg.offline_training import export as export_mod

REPO_ROOT = Path(__file__).resolve().parents[1]
DECK_PATH = REPO_ROOT / "deck.csv"
SMOKE_CONFIG = REPO_ROOT / "configs" / "offline_training_v1" / "smoke.json"

torch = pytest.importorskip("torch")


# --------------------------------------------------------------------------- #
# Shared fixtures: a collected fixture dataset and a trained checkpoint.
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def collected(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("collection")
    run_collection(
        source="fixture", run_id="cabt", games=6, base_seed=1000, output_root=root,
        canonical_base_sha="a" * 40, deck_path=DECK_PATH, repository_root=REPO_ROOT,
        validation_percent=20, split_seed=0, fixture_decisions_per_seat=4, fixture_option_count=3,
    )
    return collection_dataset_path(root, "cabt")


@pytest.fixture(scope="module")
def dataset_dir(tmp_path_factory, collected) -> Path:
    out = tmp_path_factory.mktemp("dataset") / "canonical"
    build_dataset(
        source_jsonl=collected, output_dir=out, shard_size=8, split_seed=12345,
        train_fraction=0.5, validation_fraction=0.25, test_fraction=0.25,
        teacher_id="rule-agent-v0", trainer_id="offline-training-v1", source_collection_hash="NONE",
    )
    return out


@pytest.fixture(scope="module")
def trained(tmp_path_factory, dataset_dir):
    from mage_ptcg.offline_training import neural

    ckdir = tmp_path_factory.mktemp("checkpoints")
    result = neural.train(
        dataset_dir=dataset_dir, checkpoint_dir=ckdir, hidden_dims=[64], epochs=3,
        learning_rate=3e-4, weight_decay=1e-4, grad_clip=1.0, patience=5, seed=7,
        max_batch_decisions=64, model_purpose=neural.MODEL_PURPOSE_SMOKE, device="cpu",
    )
    return ckdir, result


@pytest.fixture(scope="module")
def export_document(dataset_dir, trained):
    from mage_ptcg.offline_training import neural
    from mage_ptcg.student.artifact import feature_schema

    ckdir, _ = trained
    module, meta, spec = neural.load_module_from_checkpoint(ckdir / "best", device="cpu")
    return export_mod.build_export(
        module=module, model_spec_dict=spec.to_dict(), normalization=meta["normalization"],
        feature_schema=feature_schema(), dataset_hash=meta["dataset_hash"], config_hash="cfg",
        teacher_id="rule-agent-v0", model_purpose=meta["model_purpose"],
    )


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #


def test_config_presets_load():
    for name in ("smoke", "pilot", "production"):
        config = load_config(REPO_ROOT / "configs" / "offline_training_v1" / f"{name}.json")
        config.validate()
        assert config.profile == name


def test_config_rejects_unknown_key():
    with pytest.raises(ConfigError):
        load_config({"schema_version": "offline-training-v1-config-v1", "profile": "x", "run_id_prefix": "y", "collection": {"nope": 1}})


def test_config_rejects_bad_split_fractions():
    with pytest.raises(ConfigError):
        load_config({"schema_version": "offline-training-v1-config-v1", "profile": "x", "run_id_prefix": "y", "dataset": {"train_fraction": 0.5, "validation_fraction": 0.3, "test_fraction": 0.3}})


# --------------------------------------------------------------------------- #
# Environment
# --------------------------------------------------------------------------- #


def test_doctor_reports_fields_and_policy():
    report = doctor()
    assert report["cpu_count"] >= 1
    assert "cuda_available" in report
    policy = resolve_resource_policy(report)
    assert 2 <= policy.collection_workers <= 16
    assert policy.ram_reservation_bytes >= 6 * (1024 ** 3)


# --------------------------------------------------------------------------- #
# Run lifecycle
# --------------------------------------------------------------------------- #


def test_atomic_manifest_roundtrip(tmp_path):
    path = tmp_path / "m.json"
    runstate.atomic_write_json(path, {"a": 1, "b": [1, 2, 3]})
    assert json.loads(path.read_text()) == {"a": 1, "b": [1, 2, 3]}
    # no leftover temp siblings
    assert list(tmp_path.glob(".*tmp")) == []


def test_lock_acquire_and_active_rejection(tmp_path):
    import socket

    paths = runstate.RunPaths(tmp_path)
    paths.root.mkdir(parents=True, exist_ok=True)
    # A different, live process (pid 1) on this host holds the lock.
    other = {"pid": 1, "hostname": socket.gethostname(), "process_start_marker": None,
             "created_at": "t", "run_id": "run"}
    paths.lock.write_text(json.dumps(other))
    with pytest.raises(runstate.RunStateError):
        runstate.acquire_lock(paths, "run")


def test_stale_lock_is_recovered(tmp_path):
    paths = runstate.RunPaths(tmp_path)
    dead = {"pid": 2_147_480_000, "hostname": "h", "process_start_marker": None, "created_at": "t", "run_id": "run"}
    paths.root.mkdir(parents=True, exist_ok=True)
    paths.lock.write_text(json.dumps(dead))
    runstate.acquire_lock(paths, "run")  # dead pid -> recovered
    assert json.loads(paths.lock.read_text())["pid"] == os.getpid()


def test_load_or_create_rejects_config_mismatch(tmp_path):
    runstate.load_or_create(tmp_path, run_id="r", git_commit="c", config_hash="h1", environment_hash="e", resume=False)
    runstate.release_lock(runstate.RunPaths(tmp_path))
    with pytest.raises(runstate.RunStateError):
        runstate.load_or_create(tmp_path, run_id="r", git_commit="c", config_hash="h2", environment_hash="e", resume=True)


# --------------------------------------------------------------------------- #
# Dataset
# --------------------------------------------------------------------------- #


def test_deterministic_split_no_leakage_and_stable():
    episodes = [f"ep-{i}" for i in range(20)]
    a = deterministic_episode_split(episodes, split_seed=1, train_fraction=0.8, validation_fraction=0.1, test_fraction=0.1)
    b = deterministic_episode_split(episodes, split_seed=1, train_fraction=0.8, validation_fraction=0.1, test_fraction=0.1)
    assert a == b
    buckets = {"train": set(), "validation": set(), "test": set()}
    for ep, split in a.items():
        buckets[split].add(ep)
    assert buckets["train"] and buckets["validation"] and buckets["test"]
    assert buckets["train"].isdisjoint(buckets["validation"])
    assert buckets["train"].isdisjoint(buckets["test"])
    assert buckets["validation"].isdisjoint(buckets["test"])


def test_deterministic_split_keeps_o2_pair_together():
    episodes = [f"ep-{i}" for i in range(20)]
    # Pair up episodes 2i/2i+1 as O2 seat-swapped match pairs.
    group_key = {}
    for i in range(0, 20, 2):
        group_key[episodes[i]] = f"o2-pair:{i // 2}"
        group_key[episodes[i + 1]] = f"o2-pair:{i // 2}"
    for seed in range(30):
        assignment = deterministic_episode_split(
            episodes, split_seed=seed, train_fraction=0.6, validation_fraction=0.2, test_fraction=0.2,
            group_key_by_episode_id=group_key,
        )
        for i in range(0, 20, 2):
            assert assignment[episodes[i]] == assignment[episodes[i + 1]], f"pair {i} split at seed {seed}"


def test_deterministic_split_without_group_key_matches_prior_behavior():
    episodes = [f"ep-{i}" for i in range(20)]
    with_none = deterministic_episode_split(episodes, split_seed=1, train_fraction=0.8, validation_fraction=0.1, test_fraction=0.1)
    with_empty_group = deterministic_episode_split(
        episodes, split_seed=1, train_fraction=0.8, validation_fraction=0.1, test_fraction=0.1, group_key_by_episode_id={},
    )
    assert with_none == with_empty_group


def test_build_dataset_records_source_plan_hash_when_given(collected, tmp_path):
    manifest = build_dataset(
        source_jsonl=collected, output_dir=tmp_path / "with-plan-hash", shard_size=8, split_seed=1,
        train_fraction=0.5, validation_fraction=0.25, test_fraction=0.25,
        teacher_id="rule-agent-v0", trainer_id="offline-training-v1",
        source_collection_hash="NONE", source_plan_hash="planhash123",
    )
    assert manifest["source_plan_hash"] == "planhash123"


def test_collection_plan_fingerprint_is_none_for_missing_or_empty_list() -> None:
    from mage_ptcg.offline_training.cli import _collection_plan_fingerprint

    assert _collection_plan_fingerprint(None) == "NONE"
    assert _collection_plan_fingerprint([]) == "NONE"
    assert _collection_plan_fingerprint("not-a-list") == "NONE"


def test_collection_plan_fingerprint_is_deterministic_and_order_independent() -> None:
    from mage_ptcg.offline_training.cli import _collection_plan_fingerprint

    a = _collection_plan_fingerprint(["hash1", "hash2", "hash3"])
    b = _collection_plan_fingerprint(["hash3", "hash1", "hash2"])
    c = _collection_plan_fingerprint(["hash1", "hash2", "hash4"])
    assert a == b
    assert a != c
    assert a != "NONE"


def test_build_dataset_defaults_source_plan_hash_to_none(collected, tmp_path):
    manifest = build_dataset(
        source_jsonl=collected, output_dir=tmp_path / "without-plan-hash", shard_size=8, split_seed=1,
        train_fraction=0.5, validation_fraction=0.25, test_fraction=0.25,
        teacher_id="rule-agent-v0", trainer_id="offline-training-v1", source_collection_hash="NONE",
    )
    assert manifest["source_plan_hash"] == "NONE"


def test_dataset_split_partitions_episodes_wholly(dataset_dir):
    manifest = load_manifest(dataset_dir)
    assignment = manifest["split_assignment"]
    seen: dict[str, str] = {}
    for split in ("train", "validation", "test"):
        for decision in iter_decisions(dataset_dir, split):
            if decision.source_id in seen:
                assert seen[decision.source_id] == split
            seen[decision.source_id] = split
            assert assignment[decision.source_id] == split


def test_shard_checksums_and_corruption_rejected(dataset_dir, tmp_path):
    verify_shards(dataset_dir)
    import shutil

    corrupt = tmp_path / "corrupt"
    shutil.copytree(dataset_dir, corrupt)
    shard = load_manifest(corrupt)["shards"][0]["name"]
    (corrupt / shard).write_bytes(b"not a gzip shard")
    with pytest.raises(OfflineDatasetError):
        verify_shards(corrupt)


def test_train_only_normalization(dataset_dir):
    manifest = load_manifest(dataset_dir)
    norm = manifest["normalization"]
    # normalization count equals the number of TRAIN candidate rows only.
    train_rows = sum(len(d.candidate_features) for d in iter_decisions(dataset_dir, "train"))
    assert norm["count"] == train_rows
    assert len(norm["mean"]) == manifest["feature_dimension"]


def test_privacy_field_rejected_by_dataset(tmp_path):
    # A record carrying a forbidden observation key must be rejected on load.
    from mage_ptcg.student.dataset import DatasetValidationError

    bad = tmp_path / "bad.jsonl"
    bad.write_text(json.dumps({"schema_version": "rule-bc-v1", "example_id": "x", "source_id": "s",
                               "public_state": {"deck_order": [1, 2]}, "own_private_state": {},
                               "visible_history": [], "selection_type": 0, "selection_context": 0,
                               "min_count": 1, "max_count": 1, "legal_actions": [], "target_action_digests": [],
                               "teacher_ranking": [], "fallback_used": False, "deck_fingerprint": "d",
                               "source_revision": "r", "metadata": {}}) + "\n")
    with pytest.raises((OfflineDatasetError, DatasetValidationError)):
        build_dataset(source_jsonl=bad, output_dir=tmp_path / "out", shard_size=4, split_seed=1,
                      train_fraction=0.5, validation_fraction=0.25, test_fraction=0.25,
                      teacher_id="t", trainer_id="tr", source_collection_hash="NONE")


# --------------------------------------------------------------------------- #
# Neural training
# --------------------------------------------------------------------------- #


def test_cpu_training_is_finite(trained):
    _ckdir, result = trained
    import math

    assert math.isfinite(result["best_metric"])
    assert result["resolved"]["device"] == "cpu"


def test_checkpoint_resume_continues(dataset_dir, tmp_path):
    from mage_ptcg.offline_training import neural

    ckdir = tmp_path / "ck"
    neural.train(dataset_dir=dataset_dir, checkpoint_dir=ckdir, hidden_dims=[64], epochs=2,
                 learning_rate=3e-4, weight_decay=1e-4, grad_clip=1.0, patience=9, seed=7,
                 max_batch_decisions=64, model_purpose=neural.MODEL_PURPOSE_SMOKE, device="cpu")
    meta1 = neural.load_checkpoint_metadata(ckdir / "last")
    result = neural.train(dataset_dir=dataset_dir, checkpoint_dir=ckdir, hidden_dims=[64], epochs=4,
                          learning_rate=3e-4, weight_decay=1e-4, grad_clip=1.0, patience=9, seed=7,
                          max_batch_decisions=64, model_purpose=neural.MODEL_PURPOSE_SMOKE, device="cpu", resume=True)
    meta2 = neural.load_checkpoint_metadata(ckdir / "last")
    assert meta2["epoch"] > meta1["epoch"]
    assert result["epochs_run"] == 4


def test_checkpoint_incompatibility_rejected(dataset_dir, tmp_path):
    from mage_ptcg.offline_training import neural

    ckdir = tmp_path / "ck"
    neural.train(dataset_dir=dataset_dir, checkpoint_dir=ckdir, hidden_dims=[64], epochs=1,
                 learning_rate=3e-4, weight_decay=1e-4, grad_clip=1.0, patience=5, seed=7,
                 max_batch_decisions=64, model_purpose=neural.MODEL_PURPOSE_SMOKE, device="cpu")
    meta = neural.load_checkpoint_metadata(ckdir / "last")
    spec = neural.ModelSpec(input_dim=int(meta["model_spec"]["input_dim"]), hidden_dims=(128, 64))
    with pytest.raises(neural.NeuralError):
        neural.assert_checkpoint_compatible(meta, dataset_hash=meta["dataset_hash"],
                                            feature_schema_hash=meta["feature_schema_hash"], spec=spec,
                                            model_purpose=meta["model_purpose"])


def test_checkpoint_checksum_tamper_detected(trained):
    from mage_ptcg.offline_training import neural

    ckdir, _ = trained
    meta_path = (ckdir / "last").with_suffix(".json")
    meta = json.loads(meta_path.read_text())
    meta["epoch"] = meta["epoch"] + 100  # tamper without updating checksum
    tampered = ckdir / "tampered"
    tampered.with_suffix(".json").write_text(json.dumps(meta))
    with pytest.raises(neural.NeuralError):
        neural.load_checkpoint_metadata(tampered)


def test_injected_oom_reduces_microbatch(dataset_dir, tmp_path):
    from mage_ptcg.offline_training import neural

    calls = {"n": 0}

    def hook(_step):
        calls["n"] += 1
        return calls["n"] == 1

    result = neural.train(dataset_dir=dataset_dir, checkpoint_dir=tmp_path / "ck", hidden_dims=[64], epochs=1,
                          learning_rate=3e-4, weight_decay=1e-4, grad_clip=1.0, patience=5, seed=7,
                          max_batch_decisions=64, model_purpose=neural.MODEL_PURPOSE_SMOKE, device="cpu",
                          oom_hook=hook)
    assert result["final_microbatch"] < 64


# --------------------------------------------------------------------------- #
# Export, parity, runtime fallback
# --------------------------------------------------------------------------- #


def test_export_validates_and_hash_guards(export_document, tmp_path):
    export_mod.validate_export(export_document)
    tampered = dict(export_document)
    tampered = {**tampered, "layers": tampered["layers"][:-1]}
    with pytest.raises(export_mod.ExportError):
        export_mod.validate_export(tampered)


def test_torch_export_parity_and_order_invariance(dataset_dir, trained, export_document):
    from mage_ptcg.offline_training import neural
    import random

    ckdir, _ = trained
    module, meta, _spec = neural.load_module_from_checkpoint(ckdir / "best", device="cpu")
    mean, std = meta["normalization"]["mean"], meta["normalization"]["std"]
    module.eval()
    max_diff = 0.0
    for decision in iter_decisions(dataset_dir, "test"):
        rows = [list(r) for r in decision.candidate_features]
        feats, _mask, _t = neural._pad_batch([decision], mean, std, torch, torch.device("cpu"))
        with torch.no_grad():
            tscores = module(feats).squeeze(-1)[0][: len(rows)].tolist()
        pscores = export_mod.score_candidates(export_document, rows)
        max_diff = max(max_diff, max(abs(a - b) for a, b in zip(tscores, pscores)))
        # candidate order invariance for the pure-Python scorer
        perm = list(range(len(rows)))
        random.Random(0).shuffle(perm)
        shuffled = export_mod.score_candidates(export_document, [rows[p] for p in perm])
        for original_index, p in enumerate(perm):
            assert abs(shuffled[original_index] - pscores[p]) < 1e-9
    assert max_diff < 1e-4


def test_runtime_falls_back_on_corrupt_model(export_document):
    from mage_ptcg.offline_training.neural_runtime import NeuralRuntimePolicy

    obs = {"select": {"type": 0, "context": 0, "option": [{"type": 14}, {"type": 7, "index": 0}], "minCount": 1, "maxCount": 1},
           "current": {"energyAttached": False, "firstPlayer": 0, "players": [
               {"active": [], "asleep": False, "bench": [], "benchMax": 5, "burned": False, "confused": False, "deckCount": 53, "discard": [], "hand": [{"id": 1}], "handCount": 1, "paralyzed": False, "poisoned": False, "prize": [None] * 6},
               {"active": [], "asleep": False, "bench": [], "benchMax": 5, "burned": False, "confused": False, "deckCount": 53, "discard": [], "hand": [{"id": 2}], "handCount": 1, "paralyzed": False, "poisoned": False, "prize": [None] * 6}],
               "result": -1, "retreated": False, "stadium": [], "stadiumPlayed": False, "supporterPlayed": False, "turn": 1, "turnActionCount": 0, "yourIndex": 0}, "step": 1}
    policy = NeuralRuntimePolicy(export_document)
    choice = policy.choose(obs)
    assert choice is not None and all(0 <= i < 2 for i in choice)
    # A structurally broken document must fall back (return None).
    broken = {**export_document, "normalization": {"mean": [0.0], "std": [1.0]}}
    assert NeuralRuntimePolicy(broken).choose(obs) is None


# --------------------------------------------------------------------------- #
# Package + clean room
# --------------------------------------------------------------------------- #


def test_package_excludes_training_deps_and_verifies(export_document, tmp_path):
    from mage_ptcg.offline_training import package

    export_path = tmp_path / "export.json"
    export_mod.write_export(export_document, export_path)
    pkg_dir = tmp_path / "pkg"
    manifest = package.build_package(export_path=export_path, output_dir=pkg_dir, repository_root=REPO_ROOT, build_commit="test")
    members = {record["path"] for record in manifest["files"]}
    assert "main.py" in members and "deck.csv" in members and package.MODEL_MEMBER in members
    assert "mage_submission_agents/rule_agent.py" in members
    assert not any(name.startswith("agents/") for name in members)
    assert not any("neural.py" in name or "torch" in name or name.endswith(".pt") for name in members)
    assert manifest["clean_room"]["verified"] is True
    assert manifest["clean_room"]["legal_action_rate"] == 1.0


def test_package_rejects_unsafe_member_names(tmp_path):
    from mage_ptcg.offline_training import package

    with pytest.raises(package.PackageError):
        package._safe_path("../escape")
    with pytest.raises(package.PackageError):
        package._safe_path("/abs")


# --------------------------------------------------------------------------- #
# CLI pipeline + status
# --------------------------------------------------------------------------- #


def test_cli_pipeline_resume_and_status(tmp_path, monkeypatch):
    from mage_ptcg.offline_training import cli

    run_dir = tmp_path / "run"
    monkeypatch.setenv("MAGE_PTCG_DIST_ROOT", str(tmp_path / "dist"))
    args = ["pipeline", "--config", str(SMOKE_CONFIG), "--run-dir", str(run_dir)]
    assert cli.main(args) == 0
    manifest = json.loads((run_dir / "run_manifest.json").read_text())
    assert all(status in ("COMPLETE", "SKIPPED") for status in manifest["phase_statuses"].values())
    # resume is a no-op (everything complete)
    assert cli.main(["resume", "--run-dir", str(run_dir)]) == 0
    assert cli.main(["status", "--run-dir", str(run_dir)]) == 0


def test_cli_collection_resume_is_idempotent(tmp_path):
    from mage_ptcg.offline_training import cli

    run_dir = tmp_path / "run"
    assert cli.main(["collect", "--config", str(SMOKE_CONFIG), "--run-dir", str(run_dir)]) == 0
    jsonl = run_dir / "collection" / "cabt" / "private_dataset" / "rule-bc-v1.jsonl"
    first = jsonl.read_bytes()
    # A second collect on the completed run must not re-execute or change output.
    assert cli.main(["collect", "--config", str(SMOKE_CONFIG), "--run-dir", str(run_dir)]) == 0
    assert jsonl.read_bytes() == first


def test_derived_numpy_cache(dataset_dir, tmp_path):
    from mage_ptcg.offline_training.dataset import load_feature_cache, save_feature_cache, iter_decisions
    import shutil
    import math

    # Copy the dataset_dir to a test-isolated directory to avoid pre-existing cache interference
    test_dataset_dir = tmp_path / "test_dataset"
    shutil.copytree(str(dataset_dir), str(test_dataset_dir))

    # Ensure no copied cache remains in the temporary directory
    cache_dir = test_dataset_dir / ".derived_cache"
    if cache_dir.exists():
        shutil.rmtree(cache_dir)

    # 1. Initially no cache exists in the fresh copy
    decisions = load_feature_cache(test_dataset_dir, "train")
    assert decisions is None

    # 2. Load raw and save to cache
    raw_decisions = list(iter_decisions(test_dataset_dir, "train"))
    assert len(raw_decisions) > 0
    save_feature_cache(test_dataset_dir, "train", raw_decisions)

    # 3. Load from cache
    cached_decisions = load_feature_cache(test_dataset_dir, "train")
    assert cached_decisions is not None
    assert len(cached_decisions) == len(raw_decisions)

    # 4. Verify contents are identical
    for raw, cached in zip(raw_decisions, cached_decisions):
        assert raw.example_id == cached.example_id
        assert raw.source_id == cached.source_id
        assert raw.selection_type == cached.selection_type
        assert raw.min_count == cached.min_count
        assert raw.target_indices == cached.target_indices
        assert raw.candidate_digests == cached.candidate_digests
        for r_feat, c_feat in zip(raw.candidate_features, cached.candidate_features):
            assert len(r_feat) == len(c_feat)
            for rf, cf in zip(r_feat, c_feat):
                assert math.isclose(rf, cf, abs_tol=1e-5)

    # 5. Mismatch triggers cache rejection (e.g. altered normalization hash)
    manifest_path = Path(test_dataset_dir) / "dataset_manifest.json"
    orig_text = manifest_path.read_text("utf-8")
    manifest = json.loads(orig_text)

    manifest["normalization"]["mean"][0] += 1.0
    manifest_path.write_text(json.dumps(manifest), "utf-8")
    try:
        assert load_feature_cache(test_dataset_dir, "train") is None
    finally:
        manifest_path.write_text(orig_text, "utf-8")


def test_package_reproducibility(export_document, tmp_path):
    from mage_ptcg.offline_training import package
    import time

    export_path = tmp_path / "export_repro.json"
    export_mod.write_export(export_document, export_path)

    pkg_dir_1 = tmp_path / "pkg1"
    pkg_dir_2 = tmp_path / "pkg2"

    manifest1 = package.build_package(export_path=export_path, output_dir=pkg_dir_1, repository_root=REPO_ROOT, build_commit="test_commit")
    time.sleep(0.1)
    manifest2 = package.build_package(export_path=export_path, output_dir=pkg_dir_2, repository_root=REPO_ROOT, build_commit="test_commit")

    # Tarball SHA-256 and content bytes must be identical
    assert manifest1["archive_sha256"] == manifest2["archive_sha256"]
    tar_path_1 = pkg_dir_1 / package.ARCHIVE_NAME
    tar_path_2 = pkg_dir_2 / package.ARCHIVE_NAME
    assert tar_path_1.read_bytes() == tar_path_2.read_bytes()


def test_runtime_fallback_matrix(export_document):
    from mage_ptcg.offline_training.neural_runtime import NeuralRuntimePolicy, NeuralRuntimeError
    import copy

    # 1. NaN weights handling
    nan_doc = copy.deepcopy(export_document)
    # Set all weights and biases to NaN to ensure NaN propagation regardless of input values
    for layer in nan_doc["layers"]:
        layer["weight"] = [[float("nan")] * len(row) for row in layer["weight"]]
        layer["bias"] = [float("nan")] * len(layer["bias"])

    # Direct instantiation bypasses hash validation checks
    nan_policy = NeuralRuntimePolicy(nan_doc)

    obs = {"select": {"type": 0, "context": 0, "option": [{"type": 14}, {"type": 7, "index": 0}], "minCount": 1, "maxCount": 1},
           "current": {"energyAttached": False, "firstPlayer": 0, "players": [
               {"active": [], "asleep": False, "bench": [], "benchMax": 5, "burned": False, "confused": False, "deckCount": 53, "discard": [], "hand": [{"id": 1}], "handCount": 1, "paralyzed": False, "poisoned": False, "prize": [None] * 6},
               {"active": [], "asleep": False, "bench": [], "benchMax": 5, "burned": False, "confused": False, "deckCount": 53, "discard": [], "hand": [{"id": 2}], "handCount": 1, "paralyzed": False, "poisoned": False, "prize": [None] * 6}],
               "result": -1, "retreated": False, "stadium": [], "stadiumPlayed": False, "supporterPlayed": False, "turn": 1, "turnActionCount": 0, "yourIndex": 0}, "step": 1}

    assert nan_policy.choose(obs) is None

    # 1.1 Inf weights handling
    inf_doc = copy.deepcopy(export_document)
    for layer in inf_doc["layers"]:
        layer["weight"] = [[float("inf")] * len(row) for row in layer["weight"]]
        layer["bias"] = [float("inf")] * len(layer["bias"])
    inf_policy = NeuralRuntimePolicy(inf_doc)
    assert inf_policy.choose(obs) is None

    # 1.2 Weight shape mismatch
    shape_doc = copy.deepcopy(export_document)
    shape_doc["layers"] = []
    shape_policy = NeuralRuntimePolicy(shape_doc)
    assert shape_policy.choose(obs) is None

    # 1.3 Model / Path missing & schema mismatch
    with pytest.raises(NeuralRuntimeError):
        NeuralRuntimePolicy.load(None)

    bad_schema = copy.deepcopy(export_document)
    bad_schema["schema_version"] = "invalid-schema-v999"
    with pytest.raises(export_mod.ExportError):
        export_mod.validate_export(bad_schema)

    # 2. Unexpected observation structures
    policy = NeuralRuntimePolicy(export_document)
    assert policy.choose(None) is None
    assert policy.choose([]) is None
    assert policy.choose({"select": "not-a-dict"}) is None
    assert policy.choose({"select": {"minCount": "invalid"}}) is None

    # 2.1 Empty options
    empty_obs = copy.deepcopy(obs)
    empty_obs["select"]["option"] = []
    assert policy.choose(empty_obs) is None


def test_split_leakage_quarantine(tmp_path, collected):
    from mage_ptcg.offline_training.dataset import build_dataset, load_manifest, iter_examples, _decision_hash
    from mage_ptcg.student.dataset import load_dataset
    import dataclasses

    examples = list(load_dataset(collected))
    ex1 = examples[0]

    # Create duplicate contexts in different episodes to force cross-split leakage
    dup1 = dataclasses.replace(
        ex1,
        example_id="dup-ex-1",
        source_id="episode-leak-A",
        metadata={"decision_index": "0"}
    )

    dup2 = dataclasses.replace(
        ex1,
        example_id="dup-ex-2",
        source_id="episode-leak-B",
        metadata={"decision_index": "0"}
    )

    leak_jsonl = tmp_path / "leak_source.jsonl"
    with open(leak_jsonl, "w", encoding="utf-8") as f:
        for ex in examples:
            f.write(json.dumps(ex.to_dict()) + "\n")
        f.write(json.dumps(dup1.to_dict()) + "\n")
        f.write(json.dumps(dup2.to_dict()) + "\n")

    out_dir = tmp_path / "leak_dataset"
    manifest = build_dataset(
        source_jsonl=leak_jsonl, output_dir=out_dir, shard_size=8, split_seed=12345,
        train_fraction=0.5, validation_fraction=0.25, test_fraction=0.25,
        teacher_id="rule-agent-v0", trainer_id="offline-training-v1", source_collection_hash="NONE",
    )

    # Assert that no identical decision hashes exist across splits
    splits_hashes = {}
    for split in ("train", "validation", "test"):
        hashes = { _decision_hash(ex) for ex in iter_examples(out_dir, split) }
        splits_hashes[split] = hashes

    assert not (splits_hashes["train"] & splits_hashes["validation"])
    assert not (splits_hashes["train"] & splits_hashes["test"])
    assert not (splits_hashes["validation"] & splits_hashes["test"])


def test_signal_interruption_and_resume(tmp_path):
    import subprocess
    import signal
    import time
    from mage_ptcg.offline_training import cli
    from mage_ptcg.offline_training.config import load_config

    # Config with high epochs to ensure process runs long enough to receive signal
    config_data = {
        "schema_version": "offline-training-v1-config-v1",
        "profile": "smoke_long",
        "run_id_prefix": "offline-training-v1",
        "collection": {
            "source": "fixture",
            "games": 4,
            "base_seed": 1000,
            "validation_percent": 20,
            "split_seed": 0,
            "fixture_decisions_per_seat": 3,
            "fixture_option_count": 3
        },
        "dataset": {
            "shard_size": 8,
            "train_fraction": 0.5,
            "validation_fraction": 0.25,
            "test_fraction": 0.25,
            "split_seed": 12345
        },
        "model": {
            "preset": "compact"
        },
        "training": {
            "epochs": 100,
            "learning_rate": 0.0003,
            "weight_decay": 0.0001,
            "grad_clip": 1.0,
            "patience": 100,
            "seed": 7,
            "max_batch_decisions": 64
        },
        "screening": {
            "games": 4,
            "base_seed": 5000
        }
    }
    config_file = tmp_path / "long.json"
    config_file.write_text(json.dumps(config_data), "utf-8")

    run_dir = tmp_path / "run_interrupted"
    manifest_path = run_dir / "run_manifest.json"

    # cli.main() installs a SIGINT/SIGTERM handler on the *calling* process
    # (see cli._install_signal_guard, invoked unconditionally from
    # cli._dispatch for every subcommand). The collect/build-dataset/resume
    # calls below run in-process, so they overwrite pytest's own handlers;
    # restore them afterwards so this test cannot affect later ones.
    original_sigint = signal.getsignal(signal.SIGINT)
    original_sigterm = signal.getsignal(signal.SIGTERM)
    try:
        assert cli.main(["collect", "--config", str(config_file), "--run-dir", str(run_dir)]) == 0
        assert cli.main(["build-dataset", "--config", str(config_file), "--run-dir", str(run_dir)]) == 0

        cmd = [
            "/usr/bin/python3",
            str(REPO_ROOT / "scripts" / "run_offline_training_v1.py"),
            "train",
            "--config", str(config_file),
            "--run-dir", str(run_dir),
            "--resume"
        ]

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(REPO_ROOT)
        )

        try:
            # Wait for the train phase to actually reach RUNNING instead of
            # guessing at a fixed delay. cli._dispatch() installs the SIGINT
            # handler before phase_train() writes RUNNING to the manifest,
            # so once we observe RUNNING the subprocess is guaranteed to
            # have an armed handler and SIGINT cannot race process startup.
            deadline = time.monotonic() + 20.0
            while True:
                if time.monotonic() >= deadline:
                    pytest.fail("training did not reach RUNNING state before timeout")

                returncode = proc.poll()
                if returncode is not None:
                    stdout, stderr = proc.communicate()
                    pytest.fail(
                        "training process exited before interrupt readiness: "
                        f"returncode={returncode}\n"
                        f"stdout={stdout}\n"
                        f"stderr={stderr}"
                    )

                try:
                    manifest = json.loads(manifest_path.read_text("utf-8"))
                except (FileNotFoundError, json.JSONDecodeError):
                    time.sleep(0.02)
                    continue

                train_status = manifest.get("phase_statuses", {}).get("train")
                if train_status == "RUNNING":
                    break
                if train_status == "COMPLETE":
                    pytest.fail("training completed before interrupt-ready handshake")

                time.sleep(0.02)

            # Interrupt training
            proc.send_signal(signal.SIGINT)

            try:
                proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()

        assert proc.returncode == 130

        manifest = json.loads(manifest_path.read_text("utf-8"))
        assert manifest["phase_statuses"]["train"] == "INTERRUPTED"

        # Reduce epochs in config.resolved.json to finish instantly on resume
        resolved_config_path = run_dir / "config.resolved.json"
        resolved_config = json.loads(resolved_config_path.read_text("utf-8"))
        resolved_config["training"]["epochs"] = 1
        resolved_config_path.write_text(json.dumps(resolved_config), "utf-8")

        # Update config_hash in the run_manifest to match the modified configuration
        new_cfg = load_config(resolved_config_path)
        new_hash = new_cfg.hash()

        manifest_data = json.loads(manifest_path.read_text("utf-8"))
        manifest_data["config_hash"] = new_hash
        manifest_path.write_text(json.dumps(manifest_data), "utf-8")

        # Resume the training
        assert cli.main(["resume", "--run-dir", str(run_dir)]) == 0

        final_manifest = json.loads(manifest_path.read_text("utf-8"))
        assert final_manifest["phase_statuses"]["train"] in ("COMPLETE", "SKIPPED")
    finally:
        signal.signal(signal.SIGINT, original_sigint)
        signal.signal(signal.SIGTERM, original_sigterm)


def _build_kaggle_layout(export_document, tmp_path):
    from mage_ptcg.offline_training import package

    export_path = tmp_path / "export.json"
    export_mod.write_export(export_document, export_path)
    candidate_dir = tmp_path / "candidate"
    package.build_package(
        export_path=export_path,
        output_dir=candidate_dir,
        repository_root=REPO_ROOT,
        build_commit="test-raw-exec",
    )
    agent_root = tmp_path / "kaggle_simulations" / "agent"
    working_root = tmp_path / "kaggle" / "working"
    agent_root.mkdir(parents=True)
    working_root.mkdir(parents=True)
    with tarfile.open(candidate_dir / package.ARCHIVE_NAME, "r:gz") as archive:
        archive.extractall(agent_root, filter="data")
    return candidate_dir, agent_root, working_root


def _exec_generated_main(main_path, working_root):
    code = compile(main_path.read_text(encoding="utf-8"), str(main_path), "exec")
    env = {"__name__": "__main__"}
    original_cwd = Path.cwd()
    try:
        os.chdir(working_root)
        exec(code, env)
    finally:
        os.chdir(original_cwd)
    return env


def test_raw_exec_without_file_and_separate_cwd(export_document, tmp_path):
    _candidate, agent_root, working_root = _build_kaggle_layout(export_document, tmp_path)
    env = _exec_generated_main(agent_root / "main.py", working_root)

    assert callable(env["agent"])
    assert env["_ROOT"] == agent_root.resolve()


def test_root_resolution_uses_code_filename_not_cwd(export_document, tmp_path):
    _candidate, agent_root, working_root = _build_kaggle_layout(export_document, tmp_path)
    (working_root / "main.py").write_text("raise RuntimeError('cwd main used')\n", encoding="utf-8")
    env = _exec_generated_main(agent_root / "main.py", working_root)

    assert env["_ROOT"] == agent_root.resolve()


def test_root_resolution_rejects_empty_working_directory(tmp_path):
    from mage_ptcg.offline_training import package

    orphan_root = tmp_path / "orphan"
    working_root = tmp_path / "kaggle" / "working"
    orphan_root.mkdir()
    working_root.mkdir(parents=True)
    main_path = orphan_root / "main.py"
    main_path.write_text(package._MAIN_TEMPLATE, encoding="utf-8")

    with pytest.raises(RuntimeError, match="submission package root could not be resolved"):
        _exec_generated_main(main_path, working_root)


def test_root_resolution_does_not_read_workspace_package(export_document, tmp_path):
    _candidate, agent_root, working_root = _build_kaggle_layout(export_document, tmp_path)
    for member in (
        "main.py",
        "runtime_main.py",
        "deck.csv",
        "models/neural-student-v1.json",
        "mage_submission_agents/rule_agent.py",
        "src/mage_ptcg/offline_training/neural_runtime.py",
    ):
        target = working_root / member
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("raise RuntimeError('workspace package used')\n", encoding="utf-8")

    env = _exec_generated_main(agent_root / "main.py", working_root)
    assert env["_ROOT"] == agent_root.resolve()


def test_root_resolution_does_not_use_candidate_sidecar(tmp_path):
    from mage_ptcg.offline_training import package

    orphan_root = tmp_path / "orphan"
    candidate_sidecar = tmp_path / "candidate-sidecar"
    orphan_root.mkdir()
    candidate_sidecar.mkdir()
    main_path = orphan_root / "main.py"
    main_path.write_text(package._MAIN_TEMPLATE, encoding="utf-8")
    for member in (
        "main.py",
        "runtime_main.py",
        "deck.csv",
        "models/neural-student-v1.json",
        "mage_submission_agents/rule_agent.py",
        "src/mage_ptcg/offline_training/neural_runtime.py",
    ):
        target = candidate_sidecar / member
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("sidecar poison\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="submission package root could not be resolved"):
        _exec_generated_main(main_path, candidate_sidecar)


def test_root_resolution_fails_when_required_member_missing(export_document, tmp_path):
    _candidate, agent_root, working_root = _build_kaggle_layout(export_document, tmp_path)
    (agent_root / "models" / "neural-student-v1.json").unlink()

    with pytest.raises(RuntimeError, match="submission package root could not be resolved"):
        _exec_generated_main(agent_root / "main.py", working_root)


def test_root_resolution_works_under_python_311(export_document, tmp_path):
    _candidate, agent_root, working_root = _build_kaggle_layout(export_document, tmp_path)
    pyenv_prefix = subprocess.run(
        ["pyenv", "prefix", "3.11.11"], capture_output=True, text=True, check=True
    ).stdout.strip()
    python311 = Path(pyenv_prefix) / "bin" / "python"
    script = (
        "from pathlib import Path; "
        f"p=Path({str(agent_root / 'main.py')!r}); "
        "e={'__name__':'__main__'}; "
        "exec(compile(p.read_text(encoding='utf-8'), str(p), 'exec'), e); "
        "assert callable(e['agent']); print(e['_ROOT'])"
    )
    completed = subprocess.run(
        [str(python311), "-I", "-c", script],
        cwd=working_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert completed.stdout.strip() == str(agent_root.resolve())


def test_get_last_callable_without_file_and_separate_cwd(export_document, tmp_path):
    from kaggle_environments.agent import get_last_callable

    _candidate, agent_root, working_root = _build_kaggle_layout(export_document, tmp_path)

    main_path = agent_root / "main.py"
    raw_agent = main_path.read_text(encoding="utf-8")
    original_cwd = Path.cwd()
    try:
        os.chdir(working_root)
        callable_agent = get_last_callable(raw_agent, path=str(main_path))
    finally:
        os.chdir(original_cwd)

    assert callable(callable_agent)

    obs = {
        "current": None,
        "logs": [],
        "remainingOverageTime": 600,
        "search_begin_input": None,
        "select": None,
        "step": 0
    }
    action = callable_agent(obs)
    assert isinstance(action, list)
    assert len(action) == 60
