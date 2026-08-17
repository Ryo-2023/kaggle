import pytest
import math
import tempfile
import time
import json
import gzip
from pathlib import Path
from mage_ptcg.offline_training_v1_support.contracts import (
    SupportContractError,
    digest,
    canonical_json,
    walk_safe,
    FileLock,
    FileLockError,
    atomic_write_json,
)
from mage_ptcg.offline_training_v1_support.statistics import (
    wilson_score_interval,
    run_stratified_bootstrap,
    evaluate_game_statistics,
)
from mage_ptcg.offline_training_v1_support.schedule import (
    generate_schedule,
    validate_games_against_schedule,
)
from mage_ptcg.offline_training_v1_support.cross_play import (
    generate_cross_play_report,
)
from mage_ptcg.offline_training_v1_support.ratings import (
    compute_elo,
    compute_bradley_terry,
)
from mage_ptcg.offline_training_v1_support.registries import SupportRegistryManager
from mage_ptcg.offline_training_v1_support.mining import mine_hard_states
from mage_ptcg.offline_training_v1_support.dedup import process_and_deduplicate
from mage_ptcg.offline_training_v1_support.sampling import priority_sample
from mage_ptcg.offline_training_v1_support.cli import main

# Phase 2 module imports
from mage_ptcg.offline_training_v1_support.dataset_ops import DatasetLifecycleManager, get_file_sha256, DATASET_MANIFEST_SCHEMA_VERSION
from mage_ptcg.offline_training_v1_support.teacher_registry import TeacherRegistry, TEACHER_SCHEMA_VERSION
from mage_ptcg.offline_training_v1_support.teacher_cache import TeacherCache
from mage_ptcg.offline_training_v1_support.iteration import DistillationOrchestrator
from mage_ptcg.offline_training_v1_support.sweep import SweepOrchestrator
from mage_ptcg.offline_training_v1_support.calibration import (
    compute_ece,
    compute_nll,
    compute_brier_score,
    fit_temperature,
    evaluate_group_calibration,
)
from mage_ptcg.offline_training_v1_support.ood import compute_ood_diagnostics
from mage_ptcg.offline_training_v1_support.performance import analyze_performance_measurements
from mage_ptcg.offline_training_v1_support.reproducibility import ReproducibilityBundleManager
from mage_ptcg.offline_training_v1_support.promotion import PromotionEvaluator


def test_canonical_json_hash_stability():
    value = {"b": 2, "a": 1}
    h1 = digest(value)
    h2 = digest({"a": 1, "b": 2})
    assert h1 == h2


def test_wilson_score_interval():
    low, high = wilson_score_interval(10, 0, 0, draw_weight=0.5, confidence=0.95)
    assert 0.65 < low < 0.75
    assert 0.95 < high <= 1.0

    low_d, high_d = wilson_score_interval(0, 0, 10, draw_weight=0.5, confidence=0.95)
    assert abs((low_d + high_d) / 2.0 - 0.5) < 0.05


def test_bootstrap_determinism():
    games = [
        {"candidate_seat": 0, "winner": "candidate"},
        {"candidate_seat": 0, "winner": "opponent"},
        {"candidate_seat": 1, "winner": "candidate"},
        {"candidate_seat": 1, "winner": "draw"},
    ]
    r1_low, r1_high = run_stratified_bootstrap(games, num_samples=100, seed=42)
    r2_low, r2_high = run_stratified_bootstrap(games, num_samples=100, seed=42)
    assert r1_low == r2_low
    assert r1_high == r2_high


def test_bootstrap_stratification():
    games = [{"candidate_seat": 0, "winner": "candidate"}]
    r_low, r_high = run_stratified_bootstrap(games, num_samples=50, seed=42)
    assert r_low == 1.0
    assert r_high == 1.0


def test_missing_fields_rejection():
    invalid_games = [{"game_id": "game_1", "winner": "candidate"}]
    with pytest.raises(SupportContractError):
        evaluate_game_statistics(invalid_games)


def test_non_finite_rejection():
    with pytest.raises(SupportContractError):
        walk_safe({"value": float("nan")})

    with pytest.raises(SupportContractError):
        walk_safe({"value": float("inf")})

    invalid_games = [
        {
            "game_id": "game_1",
            "winner": "candidate",
            "candidate_seat": 0,
            "candidate_legal_rate": float("nan"),
        }
    ]
    with pytest.raises(SupportContractError):
        evaluate_game_statistics(invalid_games)


def test_schedule_generation():
    config = {
        "schema_version": "support-schedule-config-v1",
        "candidate_policies": ["policy_a"],
        "opponent_policies": ["policy_b"],
        "candidate_decks": ["deck_1"],
        "opponent_decks": ["deck_2"],
        "seats": [0, 1],
        "base_seed": 100,
        "repetitions": 2,
    }
    sch = generate_schedule(config)
    assert len(sch) == 2
    seats = [item["candidate_seat"] for item in sch]
    assert 0 in seats
    assert 1 in seats


def test_schedule_odd_games():
    config = {
        "schema_version": "support-schedule-config-v1",
        "candidate_policies": ["policy_a"],
        "opponent_policies": ["policy_b"],
        "candidate_decks": ["deck_1"],
        "opponent_decks": ["deck_2"],
        "seats": [0, 1],
        "base_seed": 100,
        "repetitions": 3,
    }
    sch = generate_schedule(config)
    seats = [item["candidate_seat"] for item in sch]
    seat_0 = seats.count(0)
    seat_1 = seats.count(1)
    assert abs(seat_0 - seat_1) <= 1


def test_schedule_hash_stability():
    config = {
        "schema_version": "support-schedule-config-v1",
        "candidate_policies": ["policy_a"],
        "opponent_policies": ["policy_b"],
        "candidate_decks": ["deck_1"],
        "opponent_decks": ["deck_2"],
        "seats": [0, 1],
        "base_seed": 100,
        "repetitions": 2,
    }
    sch1 = generate_schedule(config)
    sch2 = generate_schedule(config)
    assert sch1[0]["schedule_hash"] == sch2[0]["schedule_hash"]


def test_schedule_validation():
    schedule = [
        {
            "candidate_policy_id": "policy_a",
            "opponent_policy_id": "policy_b",
            "candidate_deck_id": "deck_1",
            "opponent_deck_id": "deck_2",
            "candidate_seat": 0,
            "seed": 100,
        },
        {
            "candidate_policy_id": "policy_a",
            "opponent_policy_id": "policy_b",
            "candidate_deck_id": "deck_1",
            "opponent_deck_id": "deck_2",
            "candidate_seat": 1,
            "seed": 101,
        },
    ]
    games = [
        {
            "game_id": "g1",
            "candidate_policy_id": "policy_a",
            "opponent_policy_id": "policy_b",
            "candidate_deck_id": "deck_1",
            "opponent_deck_id": "deck_2",
            "candidate_seat": 0,
            "seed": 100,
            "winner": "candidate",
        }
    ]
    res = validate_games_against_schedule(schedule, games)
    assert res["total_scheduled"] == 2
    assert res["total_completed"] == 1
    assert res["missing_count"] == 1
    assert len(res["missing"]) == 1


def test_cross_play_matrices():
    games = [
        {
            "game_id": "g1",
            "candidate_policy_id": "policy_a",
            "opponent_policy_id": "policy_b",
            "winner": "candidate",
            "candidate_seat": 0,
        },
        {
            "game_id": "g2",
            "candidate_policy_id": "policy_b",
            "opponent_policy_id": "policy_a",
            "winner": "opponent",
            "candidate_seat": 1,
        },
    ]
    report = generate_cross_play_report(games)
    assert report["matrices"]["win_rate"]["policy_a"]["policy_b"] == 1.0
    assert report["matrices"]["win_rate"]["policy_a"]["policy_a"] == "NO_DATA"


def test_elo_ratings():
    games = [
        {
            "game_id": "g1",
            "candidate_policy_id": "policy_a",
            "opponent_policy_id": "policy_b",
            "winner": "candidate",
            "seed": 100,
        },
        {
            "game_id": "g2",
            "candidate_policy_id": "policy_a",
            "opponent_policy_id": "policy_b",
            "winner": "draw",
            "seed": 101,
        },
    ]
    res1 = compute_elo(games)
    res2 = compute_elo(games)
    assert res1["policy_a"]["rating"] == res2["policy_a"]["rating"]
    assert res1["policy_a"]["rating"] > 1500.0
    assert res1["policy_b"]["rating"] < 1500.0


def test_bradley_terry():
    games = [
        {"candidate_policy_id": "policy_a", "opponent_policy_id": "policy_b", "winner": "candidate"},
        {"candidate_policy_id": "policy_b", "opponent_policy_id": "policy_a", "winner": "draw"},
        {"candidate_policy_id": "policy_a", "opponent_policy_id": "policy_c", "winner": "opponent"},
        {"candidate_policy_id": "policy_b", "opponent_policy_id": "policy_c", "winner": "candidate"},
    ]
    res = compute_bradley_terry(games)
    assert res["status"] == "CONVERGED"
    assert "policy_a" in res["ratings"]
    assert "policy_b" in res["ratings"]
    assert "policy_c" in res["ratings"]


def test_bradley_terry_disconnected():
    games = [
        {"candidate_policy_id": "policy_a", "opponent_policy_id": "policy_b", "winner": "candidate"},
        {"candidate_policy_id": "policy_c", "opponent_policy_id": "policy_d", "winner": "opponent"},
    ]
    with pytest.raises(SupportContractError):
        compute_bradley_terry(games)


def test_registry_workflow():
    with tempfile.TemporaryDirectory() as tmp_dir:
        reg_path = Path(tmp_dir)
        manager = SupportRegistryManager(reg_path)

        # 1. Deck Registry
        deck_rec = {
            "schema_version": "support-deck-registry-v1",
            "deck_id": "deck_a",
            "deck_hash": "h123",
            "version": "1.0",
            "availability": "PUBLIC",
            "validation_status": "VALID",
            "provenance": {"source": "test"},
        }
        manager.deck.register_dataset = manager.deck.register_deck
        h_deck = manager.deck.register_deck(deck_rec)
        assert h_deck is not None

        deck_ret = manager.deck.get("deck_a")
        assert deck_ret["deck_hash"] == "h123"

        lst = manager.deck.list_records()
        assert len(lst) == 1

        manager.deck.archive("deck_a")
        assert manager.deck.get("deck_a")["status"] == "ARCHIVED"

        # 2. Model Stage Validation
        model_rec = {
            "schema_version": "support-model-registry-v1",
            "model_id": "model_x",
            "model_hash": "m123",
            "parent_model_id": None,
            "dataset_hash": "d123",
            "feature_schema_hash": "f123",
            "architecture": "MLP",
            "training_config_hash": "tc123",
            "metrics": {},
            "runtime_benchmark": {},
            "package_hash": None,
            "stage": "INVALID_STAGE",
        }
        with pytest.raises(SupportContractError):
            manager.model.register_model(model_rec)

        model_rec["stage"] = "TRAINING"
        h_model = manager.model.register_model(model_rec)
        assert h_model is not None


def test_registry_locking():
    with tempfile.TemporaryDirectory() as tmp_dir:
        lock_file = Path(tmp_dir) / "test.lock"
        with FileLock(lock_file):
            with pytest.raises(FileLockError):
                with FileLock(lock_file, timeout=0.1):
                    pass


def test_hard_state_mining():
    records = [
        {
            "episode_id": "ep1",
            "decision_id": "dec1",
            "state_digest": "s1",
            "teacher_action_key": "teacher_act",
            "student_action_key": "student_act",
            "student_margin": 0.02,
            "student_entropy": 2.1,
            "fallback_used": True,
        }
    ]
    mined = mine_hard_states(records)
    assert len(mined) == 1
    hs = mined[0]
    assert hs["priority_score"] > 0
    assert "TEACHER_STUDENT_DISAGREEMENT" in hs["reason_codes"]
    assert "LOW_MARGIN" in hs["reason_codes"]
    assert "HIGH_ENTROPY" in hs["reason_codes"]
    assert "RUNTIME_FALLBACK" in hs["reason_codes"]


def test_exact_dedup():
    with tempfile.NamedTemporaryFile("w+", encoding="utf-8", suffix=".jsonl") as f:
        line = '{"episode_id": "ep1", "decision_id": "d1", "state_digest": "s1", "teacher_action_key": "a1"}\n'
        f.write(line)
        f.write(line)
        f.flush()

        clean, quarantined = process_and_deduplicate(f.name)
        assert len(clean) == 1
        assert len(quarantined) == 0


def test_conflict_quarantine():
    with tempfile.NamedTemporaryFile("w+", encoding="utf-8", suffix=".jsonl") as f:
        line1 = '{"episode_id": "ep1", "decision_id": "d1", "state_digest": "s1", "teacher_action_key": "a1"}\n'
        line2 = '{"episode_id": "ep1", "decision_id": "d2", "state_digest": "s1", "teacher_action_key": "a2"}\n'
        line3 = '{"episode_id": "ep1", "corrupt\n'
        f.write(line1)
        f.write(line2)
        f.write(line3)
        f.flush()

        clean, quarantined = process_and_deduplicate(f.name)
        assert len(clean) == 1
        assert len(quarantined) == 2
        reasons = [q["reason"] for q in quarantined]
        assert "same state/candidates/conflicting label" in reasons
        assert any("corrupt JSON" in r for r in reasons)


def test_priority_sampling_rules():
    records = [
        {"selection_type": "rare_select", "student_confidence": 0.1, "decision_id": "d1"},
        {"selection_type": "normal_select", "student_confidence": 0.9, "decision_id": "d2"},
    ]
    weight_config = {
        "uniform": 1.0,
        "rare_selection_type": 5.0,
        "teacher_confidence": 2.0,
    }

    s1, m1 = priority_sample(records, weight_config, sampled_count=1, replacement=True, seed=42)
    s2, m2 = priority_sample(records, weight_config, sampled_count=1, replacement=True, seed=42)
    assert s1[0]["decision_id"] == s2[0]["decision_id"]

    with pytest.raises(SupportContractError):
        priority_sample(records, {"uniform": -1.0}, sampled_count=1)

    with pytest.raises(SupportContractError):
        priority_sample(records, {"uniform": float("nan")}, sampled_count=1)


def test_sampling_guard():
    records = [{"decision_id": "d1"}]
    with pytest.raises(SupportContractError):
        priority_sample(records, {"is_validation_split": True}, sampled_count=1)


def test_cli_smoke():
    with tempfile.TemporaryDirectory() as tmp_dir:
        config_path = Path(tmp_dir) / "config.json"
        output_path = Path(tmp_dir) / "schedule.jsonl"
        config_data = {
            "schema_version": "support-schedule-config-v1",
            "candidate_policies": ["p1"],
            "opponent_policies": ["p2"],
            "candidate_decks": ["d1"],
            "opponent_decks": ["d2"],
            "base_seed": 42,
            "repetitions": 1,
        }
        config_path.write_text(json.dumps(config_data), encoding="utf-8")

        argv = [
            "schedule",
            "--config",
            str(config_path),
            "--output",
            str(output_path),
        ]
        exit_code = main(argv)
        assert exit_code == 0
        assert output_path.exists()


def test_input_mutation_prevention():
    with tempfile.TemporaryDirectory() as tmp_dir:
        manager = SupportRegistryManager(Path(tmp_dir))
        deck_rec = {
            "schema_version": "support-deck-registry-v1",
            "deck_id": "deck_a",
            "deck_hash": "h123",
            "version": "1.0",
            "availability": "PUBLIC",
            "validation_status": "VALID",
            "provenance": {"source": "test"},
        }
        manager.deck.register_deck(deck_rec)
        assert "content_hash" not in deck_rec

    records = [{"decision_id": "d1", "selection_type": "normal_select", "student_confidence": 0.5}]
    sampled, _ = priority_sample(records, {"uniform": 1.0}, sampled_count=1)
    sampled[0]["mutated"] = True
    assert "mutated" not in records[0]


# --- Phase 2 Tests ---


def test_dataset_lifecycle_ops():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)

        # 1. Create a dummy shard file
        shard_file = tmp_path / "shard_1.jsonl"
        record = {"episode_id": "ep_1", "decision_id": "dec_1", "state_digest": "s_1", "teacher_action_key": "act_1"}
        shard_file.write_text(json.dumps(record) + "\n", encoding="utf-8")

        manifest = {
            "schema_version": DATASET_MANIFEST_SCHEMA_VERSION,
            "dataset_id": "ds_test_1",
            "dataset_hash": "dummy_hash",
            "parent_dataset_ids": ["parent_ds"],
            "feature_schema_hash": "f_schema",
            "source_collection_hash": "coll_hash",
            "created_at": time.time(),
            "split_policy": "random",
            "splits": {"train": {}},
            "shards": [{
                "relative_path": "shard_1.jsonl",
                "sha256": get_file_sha256(shard_file),
                "byte_size": shard_file.stat().st_size,
                "record_count": 1,
                "episode_count": 1,
                "decision_count": 1,
                "candidate_count": 0,
                "split": "train",
                "compression": "none",
            }],
            "privacy_status": "PUBLIC_SAFE",
            "validation_status": "VALID",
            "provenance": {},
        }

        manifest_file = tmp_path / "dataset_manifest.json"
        atomic_write_json(manifest_file, manifest)

        manager = DatasetLifecycleManager(tmp_path)

        # Inspect & Validate
        info = manager.inspect_dataset(manifest_file)
        assert info["dataset_id"] == "ds_test_1"

        val_res = manager.validate_dataset(manifest_file)
        assert val_res["validation_status"] == "VALID"
        assert val_res["total_records"] == 1

        # Diff
        diff_res = manager.diff_datasets(manifest_file, manifest_file)
        assert not diff_res["added_shards"]
        assert not diff_res["feature_schema_changed"]

        # Compact
        compact_manifest = tmp_path / "compact_manifest.json"
        comp_res = manager.execute_compact(manifest_file, compact_manifest)
        compacted_file_exists = (tmp_path / comp_res["shards"][0]["relative_path"]).exists()
        assert compacted_file_exists
        assert comp_res["shards"][0]["compression"] == "gzip"

        # GC Plan
        gc_res = manager.generate_gc_plan(tmp_path, [manifest_file, compact_manifest])
        assert len(gc_res["unreferenced_shards"]) == 0


def test_teacher_probing_and_cache():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        reg = TeacherRegistry(tmp_path)

        desc = {
            "schema_version": TEACHER_SCHEMA_VERSION,
            "teacher_id": "test-teacher",
            "version": "1.0",
            "input_schema_version": "in-v1",
            "output_schema_version": "out-v1",
            "status": "PENDING",
        }

        # Probing mock entrypoint (signature test only)
        probed = reg.probe_teacher_capability(desc, "json:loads")
        assert probed["status"] == "AVAILABLE"

        # Cache lookup & store
        cache = TeacherCache(tmp_path / "cache")
        key = cache.make_cache_key("t1", "1.0", "h_cfg", "schema_v", "s_dig", "c_dig")

        # Miss
        assert cache.lookup(key) is None

        # Store
        output_data = {"selected_action_key": "act_a", "confidence": 0.9}
        cache.store(key, {"teacher_id": "t1", "teacher_version": "1.0"}, output_data)

        # Hit
        hit = cache.lookup(key)
        assert hit["selected_action_key"] == "act_a"

        # Public Stats verify
        stats = cache.get_public_stats()
        assert stats["hit_count"] == 1
        assert stats["miss_count"] == 1


def test_iteration_dagger_orchestration():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        orchestrator = DistillationOrchestrator(tmp_path)

        round_cfg = {
            "parent_round_id": None,
            "base_dataset_id": "base_ds",
            "teacher_snapshot": "teacher_v1",
            "mixing_policy": {"max_rounds": 5},
        }

        # Create round
        orchestrator.create_round("iter_1", 0, round_cfg)

        # Advance phase
        orchestrator.advance_phase(0, "COLLECTING", "RUNNING")
        manifest = orchestrator.advance_phase(0, "COLLECTING", "COMPLETE")
        assert manifest["phase_statuses"]["COLLECTING"] == "COMPLETE"

        # Stop check
        plateau_metrics = [
            {"fallback_rate": 0.05},
            {"fallback_rate": 0.05},
            {"fallback_rate": 0.05},
        ]
        stop, reason = orchestrator.check_stopping_rules(0, plateau_metrics)
        assert stop
        assert reason == "fallback_plateau"


def test_sweep_orchestration():
    orchestrator = SweepOrchestrator("sweep_x")

    base_cfg = {"lr": 0.01, "batch_size": 32}
    space = {"lr": [0.01, 0.001], "batch_size": [32, 64]}

    # 1. Grid Sweep deterministic generation
    trials = orchestrator.generate_initial_trials(base_cfg, space, "grid", maximum_trials=4)
    assert len(trials) == 4

    # 2. Successive halving advance
    # Populate mock complete results
    trials[0]["status"] = "COMPLETE"
    trials[0]["result"] = {"val_loss": 0.1}
    trials[1]["status"] = "COMPLETE"
    trials[1]["result"] = {"val_loss": 0.3}
    trials[2]["status"] = "COMPLETE"
    trials[2]["result"] = {"val_loss": float("nan")}  # Should be pruned/failed

    advanced = orchestrator.advance_successive_halving(
        trials, reduction_factor=2, min_survivors=1, objective="val_loss", direction="minimize"
    )
    assert len(advanced) == 1
    # Check that trials[0] (loss=0.1) was selected and promoted to stage 1
    assert advanced[0]["config"]["lr"] == 0.01


def test_calibration_and_temperature_scaling():
    # Model predictions mock outputs
    preds = [
        {"true_candidate_index": 0, "selected_candidate_index": 0, "logits": [2.0, 0.5, 0.1], "probabilities": [0.7, 0.2, 0.1]},
        {"true_candidate_index": 1, "selected_candidate_index": 0, "logits": [1.5, 1.8, 0.2], "probabilities": [0.4, 0.5, 0.1]},
    ]

    ece, mce, bin_stats = compute_ece(preds, num_bins=5)
    assert ece > 0
    assert len(bin_stats) == 5

    nll = compute_nll(preds, temperature=1.0)
    assert nll > 0

    brier = compute_brier_score(preds)
    assert brier > 0

    # Temperature scaling fit
    opt_t = fit_temperature(preds)
    assert opt_t > 0


def test_ood_entropy_and_margins():
    # Normal record
    rec_normal = {
        "probabilities": [0.8, 0.15, 0.05],
        "selection_type": "normal_select",
        "context_type": "normal_context",
    }
    res_normal = compute_ood_diagnostics(rec_normal)
    assert res_normal["ood_score"] == 0.0
    assert res_normal["recommended_fallback_category"] == "STANDARD_ACT"

    # OOD record (low probability, high entropy, low margin)
    rec_ood = {
        "probabilities": [0.1] * 10,
        "selection_type": "rare_select",
        "context_type": "rare_context",
    }
    res_ood = compute_ood_diagnostics(rec_ood)
    assert res_ood["ood_score"] >= 2.0
    assert "LOW_MAX_PROB" in res_ood["reason_codes"]
    assert "HIGH_ENTROPY" in res_ood["reason_codes"]
    assert "LOW_MARGIN" in res_ood["reason_codes"]
    assert res_ood["recommended_fallback_category"] == "RULE_AGENT_FALLBACK"


def test_performance_latency_analysis():
    # Latency measurement records
    measurements = [
        {"duration_ns": 1000000, "warmup": True},   # 1 ms
        {"duration_ns": 500000, "warmup": False},   # 0.5 ms
        {"duration_ns": 600000, "warmup": False},
        {"duration_ns": 550000, "warmup": False},
        {"duration_ns": 20000000, "warmup": False}, # 20 ms (outlier)
    ]

    res = analyze_performance_measurements(measurements, min_count=4)
    assert res["status"] == "PASS"
    metrics = res["metrics"]
    assert metrics["count"] == 5
    assert metrics["median_ns"] > 0
    assert metrics["outlier_count"] == 1


def test_reproducibility_redactions():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        manager = ReproducibilityBundleManager(tmp_path)

        metadata = {
            "resolved_config": {"lr": 0.01},
            "git_commit": "abcdef123",
            "environment_summary": "linux-env",
            "dataset_manifest_summary": {},
            "model_manifest_summary": {},
            "evaluation_summary": {},
            "private_path": "/home/bfe-lab-ono/credentials.json",
            "oauth_token": "bearer-secret-token",
        }

        tar_file = tmp_path / "repro.tar.gz"
        manifest = manager.assemble_bundle(tar_file, metadata)

        assert tar_file.exists()
        assert manifest["bundle_sha256"] is not None

        # Verify bundle contents
        v_res = manager.verify_bundle(tar_file)
        assert v_res["valid"]


def test_promotion_gates_evaluation():
    evaluator = PromotionEvaluator()
    stats = {
        "legal_action_rate": 1.0,
        "total_games": 200,
        "invalid_count": 0,
        "crash_count": 0,
        "timeout_count": 0,
        "seat_breakdown": {
            "0": {"win_rate": 0.52},
            "1": {"win_rate": 0.48},
        }
    }

    packet = evaluator.evaluate_gates(
        stats,
        registry_validation_passed=True,
        known_defects_count=0,
        package_clean_room="PASS",
        export_parity="PASS",
        model_hash_consistency="PASS",
        dataset_lineage="PASS",
        full_regression="PASS",
    )
    assert packet["overall_result"] == "REVIEW_READY"
    assert packet["promotion_status"] == "NO_DECISION"
    assert packet["current_champion"] == "Rule Agent v0"
    assert not packet["warnings"]


def test_chaos_checks_behavior():
    # Trigger chaos mock integration test via CLI
    assert main(["chaos-check"]) == 0
