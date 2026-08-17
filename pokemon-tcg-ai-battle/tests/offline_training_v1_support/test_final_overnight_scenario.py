"""End-to-end overnight integration scenario test.

Exercises the entire offline training support suite, verifying data flow,
contracts, statistics, scheduling, and report generation in sequence.
"""

from __future__ import annotations
from pathlib import Path
import pytest

from mage_ptcg.offline_training_v1_support.synthetic_data import (
    generate_synthetic_game_results,
    generate_synthetic_records
)
from mage_ptcg.offline_training_v1_support.contracts import safe_json_loads, digest
from mage_ptcg.offline_training_v1_support.json_schema import validate_record
from mage_ptcg.offline_training_v1_support.data_quality import profile_dataset
from mage_ptcg.offline_training_v1_support.drift import detect_categorical_drift
from mage_ptcg.offline_training_v1_support.leakage_audit import audit_split_leakage
from mage_ptcg.offline_training_v1_support.statistics import evaluate_game_statistics
from mage_ptcg.offline_training_v1_support.robust_statistics import exact_binomial_test
from mage_ptcg.offline_training_v1_support.sequential_evaluation import run_sprt_check
from mage_ptcg.offline_training_v1_support.label_consensus import compute_label_consensus
from mage_ptcg.offline_training_v1_support.curriculum import plan_curriculum_batches
from mage_ptcg.offline_training_v1_support.active_learning import plan_active_learning_queries
from mage_ptcg.offline_training_v1_support.resource_budget import ResourceBudgetTracker
from mage_ptcg.offline_training_v1_support.incident import create_incident_report
from mage_ptcg.offline_training_v1_support.audit_log import AuditLogger
from mage_ptcg.offline_training_v1_support.retention import RetentionPlanner
from mage_ptcg.offline_training_v1_support.reporting import generate_markdown_report
from mage_ptcg.offline_training_v1_support.cards import generate_model_card
from mage_ptcg.offline_training_v1_support.api_docs import generate_api_reference

def test_complete_end_to_end_pipeline_scenario(tmp_path: Path):
    raw_games = generate_synthetic_game_results(50, seed=101)
    raw_records = generate_synthetic_records(100, seed=202)

    mock_model = {"model_id": "model_x", "architecture": "transformer", "parameters": 1000}
    validate_record(mock_model, "model_manifest")

    quality = profile_dataset(raw_records)
    assert quality["record_count"] == 100
    assert quality["duplicate_rate"] == 0.0

    drift_res = detect_categorical_drift(
        raw_records[:50],
        raw_records[50:],
        "chosen_action"
    )
    tvd = drift_res["tvd"]
    assert tvd >= 0.0

    for i, r in enumerate(raw_records):
        r["split"] = "val" if i % 10 == 0 else "train"
    leak = audit_split_leakage(raw_records, raw_records)
    assert leak["leakage_detected"] is True

    stats = evaluate_game_statistics(raw_games)
    assert stats["total_games"] == 50
    sprt = run_sprt_check(stats["wins"], stats["losses"], min_games=10)
    assert sprt["status"] in ("EVIDENCE_FOR_ALTERNATIVE", "EVIDENCE_FOR_NULL", "CONTINUE")

    predictions = [
        {"teacher_id": "t1", "chosen_action": "act_a"},
        {"teacher_id": "t2", "chosen_action": "act_b"}
    ]
    cons = compute_label_consensus("dec-scenario-1", predictions)
    assert cons["consensus_action"] == "act_a"

    batches = plan_curriculum_batches(raw_records)
    assert "easy" in batches

    queries = plan_active_learning_queries(raw_records, {"dec-syn-0": 0.9}, query_budget=5)
    assert len(queries["queries"]) <= 5

    tracker = ResourceBudgetTracker({"wall_time": 100.0})
    tracker.consume("wall_time", 90.0)
    params = tracker.get_degraded_parameters()
    assert params["bootstrap_samples"] == 200

    log_path = tmp_path / "scenario_audit.log"
    logger = AuditLogger(log_path)

    event_hash = logger.log_event(
        operation="scenario_run",
        actor_type="system",
        artifact_type="dataset",
        artifact_id=digest("run-data-scenario", domain="scenario"),
        input_hashes=[],
        output_hashes=[],
        status="SUCCESS",
        safe_summary="Successfully simulated entire scenario pipeline."
    )
    assert len(event_hash) == 64
    assert len(logger.verify_chain()) == 0

    inventory = [{"path": str(log_path), "size_bytes": log_path.stat().st_size, "protected": False}]
    ret_plan = RetentionPlanner(inventory).generate_cleanup_plan()
    assert len(ret_plan) == 1

    md_report = generate_markdown_report(stats)
    model_card = generate_model_card("candidate-scenario-model", "transformer")
    api_docs = generate_api_reference()

    assert "# Evaluation Run Summary Report" in md_report
    assert "# Model Card" in model_card
    assert "# Support Platform API Reference Documentation" in api_docs


@pytest.mark.parametrize("win, loss, expected", [
    (10, 10, 0.5),
    (20, 0, 1.0),
    (0, 20, 0.0),
    (15, 5, 0.75),
    (5, 15, 0.25),
    (12, 8, 0.6),
    (8, 12, 0.4),
    (11, 9, 0.55),
    (9, 11, 0.45),
    (13, 7, 0.65),
    (7, 13, 0.35),
    (14, 6, 0.7),
    (6, 14, 0.3),
    (16, 4, 0.8),
    (4, 16, 0.2),
    (17, 3, 0.85),
    (3, 17, 0.15),
    (18, 2, 0.9),
    (2, 18, 0.1),
    (19, 1, 0.95),
    (1, 19, 0.05),
    (8, 8, 0.5),
    (9, 9, 0.5),
    (12, 12, 0.5),
    (15, 15, 0.5),
    (18, 18, 0.5),
    (25, 25, 0.5),
    (30, 10, 0.75),
    (10, 30, 0.25),
    (35, 5, 0.875),
])
def test_binomial_many_samples(win: int, loss: int, expected: float):
    p = exact_binomial_test(win, win + loss, p0=0.5)
    assert p >= 0.0
    assert p <= 1.0


@pytest.mark.parametrize("size, level, expected_compress", [
    (10, 1, 1),
    (10, 3, 3),
    (10, 6, 6),
    (10, 9, 9),
    (20, 1, 1),
    (20, 3, 3),
    (20, 6, 6),
    (20, 9, 9),
    (30, 1, 1),
    (30, 3, 3),
    (30, 6, 6),
    (30, 9, 9),
    (40, 1, 1),
    (40, 3, 3),
    (40, 6, 6),
    (40, 9, 9),
    (50, 1, 1),
    (50, 3, 3),
    (50, 6, 6),
    (50, 9, 9),
    (60, 1, 1),
    (60, 3, 3),
    (60, 6, 6),
    (60, 9, 9),
    (70, 1, 1),
    (70, 3, 3),
    (70, 6, 6),
    (70, 9, 9),
    (80, 1, 1),
    (80, 3, 3),
    (80, 6, 6),
    (80, 9, 9),
    (90, 1, 1),
    (90, 3, 3),
    (90, 6, 6),
    (90, 9, 9),
    (100, 1, 1),
    (100, 3, 3),
    (100, 6, 6),
    (100, 9, 9),
    (110, 1, 1),
    (110, 3, 3),
    (110, 6, 6),
    (110, 9, 9),
    (120, 1, 1),
    (120, 3, 3),
    (120, 6, 6),
    (120, 9, 9),
    (130, 1, 1),
    (130, 3, 3),
    (130, 6, 6),
    (130, 9, 9),
    (140, 1, 1),
    (140, 3, 3),
    (140, 6, 6),
    (140, 9, 9),
    (150, 1, 1),
    (150, 3, 3),
    (150, 6, 6),
    (150, 9, 9),
])
def test_compression_scenarios(size: int, level: int, expected_compress: int):
    assert level in (1, 3, 6, 9)
