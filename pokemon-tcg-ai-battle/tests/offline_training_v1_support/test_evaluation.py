"""Tests for Pareto analysis, evaluation planning, meta-evaluation, and experiment query engine."""

from __future__ import annotations
import pytest
from mage_ptcg.offline_training_v1_support.candidate_analysis import CandidateAnalyzer
from mage_ptcg.offline_training_v1_support.evaluation_planner import EvaluationPlanner
from mage_ptcg.offline_training_v1_support.metric_audit import audit_metric_definition
from mage_ptcg.offline_training_v1_support.experiment_query import ExperimentQueryEngine

def test_candidate_pareto_analysis():
    candidates = {
        "candidate-1": {
            "win_rate": 0.55,
            "legal_rate": 1.0,
            "crash_rate": 0.0,
            "latency_p95": 80.0
        },
        "candidate-2": {
            "win_rate": 0.52,
            "legal_rate": 1.0,
            "crash_rate": 0.02, # Higher crash rate (dominated)
            "latency_p95": 90.0
        },
        "candidate-3": {
            "win_rate": 0.48,
            "legal_rate": 0.98,
            "crash_rate": 0.05, # Bypasses safety limit
            "latency_p95": 120.0
        }
    }

    safety_limits = {
        "crash_rate": 0.03,
        "legal_rate": 0.99
    }

    analyzer = CandidateAnalyzer()
    res = analyzer.analyze_candidates(candidates, safety_limits)

    verdicts = res["verdicts"]
    assert verdicts["candidate-1"]["verdict"] == "REVIEW_CANDIDATE"
    assert verdicts["candidate-2"]["verdict"] == "DOMINATED"
    assert verdicts["candidate-3"]["verdict"] == "SAFETY_BLOCKED"
    assert res["pareto_frontier"] == ["candidate-1"]

def test_evaluation_planner_power():
    planner = EvaluationPlanner()
    plan = planner.plan_sample_size(
        baseline_win_rate=0.50,
        target_improvement=0.10,
        confidence_level=0.95,
        power_target=0.80,
        invalid_rate_estimate=0.05
    )

    assert plan["recommended_total_games"] > 0
    assert plan["games_per_seat"] * 2 == plan["recommended_total_games"]
    assert plan["recommended_total_games"] % 2 == 0

def test_meta_evaluation_definitions():
    meta = audit_metric_definition("win_rate")
    assert meta["direction"] == "higher"
    assert meta["range"] == [0.0, 1.0]

    with pytest.raises(KeyError):
        audit_metric_definition("non_existent_metric")

def test_experiment_query_engine():
    records = [
        {"model_id": "m1", "architecture": "transformer", "win_rate": 0.55},
        {"model_id": "m2", "architecture": "cnn", "win_rate": 0.52},
        {"model_id": "m3", "architecture": "transformer", "win_rate": 0.59}
    ]

    engine = ExperimentQueryEngine(records)

    # Filter by architecture
    res_filter = engine.query(filters={"architecture": "transformer"})
    assert len(res_filter) == 2
    assert "m2" not in [x["model_id"] for x in res_filter]

    # Sort by win_rate descending
    res_sort = engine.query(sort_by="win_rate", reverse=True)
    assert res_sort[0]["model_id"] == "m3"
    assert res_sort[2]["model_id"] == "m2"
