"""Phase 3 Integration adapters, compatibility, and experiment comparison tests.
"""

from __future__ import annotations

import tempfile
import json
from pathlib import Path

import pytest

from mage_ptcg.offline_training_v1_support.integration_adapters import ClaudeIntegrationAdapter
from mage_ptcg.offline_training_v1_support.compatibility import CompatibilityChecker
from mage_ptcg.offline_training_v1_support.comparison import ExperimentComparer
from mage_ptcg.offline_training_v1_support.candidate_analysis import CandidateAnalyzer
from mage_ptcg.offline_training_v1_support.evaluation_planner import EvaluationPlanner
from mage_ptcg.offline_training_v1_support.teacher_ensemble import TeacherEnsemble
from mage_ptcg.offline_training_v1_support.query_budget import QueryBudgetAllocator


def test_claude_integration_adapter():
    adapter = ClaudeIntegrationAdapter()

    # Valid dataset manifest
    claude_data = {
        "dataset_name": "test-dataset",
        "files": [{"path": "shard1.jsonl.gz", "checksum": "abc"}],
        "student_confidence": 0.8,
    }
    res = adapter.adapt("dataset_manifest", claude_data)
    assert res["status"] == "COMPATIBLE_WITH_DEFAULTS"

    # Invalid structure - should trigger PRIVACY_REJECTED due to forbidden key
    bad_data = {
        "dataset_name": "test-dataset",
        "files": [{"path": "shard1.jsonl.gz", "checksum": "abc"}],
        "token": "secret"
    }
    res_bad = adapter.adapt("dataset_manifest", bad_data)
    assert res_bad["status"] == "PRIVACY_REJECTED"


def test_compatibility_checker():
    checker = CompatibilityChecker()
    schema_a = {
        "version": "1.0.0",
        "required": ["episode_id"],
        "optional": ["student_confidence"],
        "types": {
            "episode_id": "string",
            "student_confidence": "number"
        }
    }
    schema_b = {
        "version": "1.1.0",
        "required": ["episode_id"],
        "optional": ["student_confidence", "new_optional"],
        "types": {
            "episode_id": "string",
            "student_confidence": "number",
            "new_optional": "string"
        }
    }
    schema_c = {
        "version": "2.0.0",
        "required": ["episode_id"],
        "types": {
            "episode_id": "integer" # breaking change type
        }
    }

    res_ok = checker.analyze(schema_a, schema_b)
    assert res_ok["backward_compatible"]
    assert not res_ok["breaking_reasons"]

    res_break = checker.analyze(schema_a, schema_c)
    assert not res_break["backward_compatible"]
    assert len(res_break["breaking_reasons"]) > 0
    assert "field_type_changed" in res_break["breaking_reasons"][0]


def test_experiment_comparer():
    comparer = ExperimentComparer()

    games_a = [
        {"game_id": "g1", "candidate_policy_id": "c1", "opponent_policy_id": "op", "winner": "candidate", "seed": 42, "candidate_deck_id": "d1", "candidate_seat": 0},
        {"game_id": "g2", "candidate_policy_id": "c1", "opponent_policy_id": "op", "winner": "candidate", "seed": 43, "candidate_deck_id": "d1", "candidate_seat": 1},
        {"game_id": "g3", "candidate_policy_id": "c1", "opponent_policy_id": "op", "winner": "draw", "seed": 44, "candidate_deck_id": "d2", "candidate_seat": 0},
        {"game_id": "g4", "candidate_policy_id": "c1", "opponent_policy_id": "op", "winner": "opponent", "seed": 45, "candidate_deck_id": "d2", "candidate_seat": 1},
    ]
    games_b = [
        {"game_id": "g1", "candidate_policy_id": "c2", "opponent_policy_id": "op", "winner": "opponent", "seed": 42, "candidate_deck_id": "d1", "candidate_seat": 0},
        {"game_id": "g2", "candidate_policy_id": "c2", "opponent_policy_id": "op", "winner": "opponent", "seed": 43, "candidate_deck_id": "d1", "candidate_seat": 1},
        {"game_id": "g3", "candidate_policy_id": "c2", "opponent_policy_id": "op", "winner": "draw", "seed": 44, "candidate_deck_id": "d2", "candidate_seat": 0},
        {"game_id": "g4", "candidate_policy_id": "c2", "opponent_policy_id": "op", "winner": "opponent", "seed": 45, "candidate_deck_id": "d2", "candidate_seat": 1},
    ]

    res = comparer.compare_paired(games_a, games_b)
    assert "status" in res
    assert res["total_pairs"] == 4
    assert "confidence_interval" in res


def test_candidate_analyzer():
    analyzer = CandidateAnalyzer()

    candidates = {
        "c1": {"win_rate": 0.55, "crash_rate": 0.0, "latency_p95": 120.0},
        "c2": {"win_rate": 0.58, "crash_rate": 0.01, "latency_p95": 150.0}, # fails safety (crash > 0.0)
        "c3": {"win_rate": 0.50, "crash_rate": 0.0, "latency_p95": 90.0},
    }

    safety_limits = {
        "crash_rate": 0.0,
        "latency_p95": 200.0,
    }

    res = analyzer.analyze_candidates(candidates, safety_limits)
    assert "pareto_frontier" in res
    # c2 is rejected by safety
    assert res["verdicts"]["c2"]["verdict"] == "SAFETY_BLOCKED"
    # c1 and c3 are on pareto
    pareto_ids = res["pareto_frontier"]
    assert "c1" in pareto_ids
    assert "c3" in pareto_ids


def test_evaluation_planner():
    planner = EvaluationPlanner()
    res = planner.plan_sample_size(0.5, 0.05)
    assert res["assumptions"]["confidence_level"] == 0.95
    assert res["recommended_total_games"] > 0


def test_teacher_ensemble():
    ensemble = TeacherEnsemble()

    # Majority voting format
    votes = {
        "dec_1": {
            "t1": {"action": "act_1", "confidence": 0.9},
            "t2": {"action": "act_1", "confidence": 0.8},
            "t3": {"action": "act_2", "confidence": 0.95},
        }
    }
    res = ensemble.aggregate_votes(votes)
    assert res["results"]["dec_1"]["action"] == "act_1" # 2 vs 1
    assert res["results"]["dec_1"]["status"] == "CONFLICT_RESOLVED"


def test_query_budget():
    allocator = QueryBudgetAllocator()

    records = [
        {"decision_id": "d1", "student_confidence": 0.9},
        {"decision_id": "d2", "student_confidence": 0.4},
        {"decision_id": "d3", "student_confidence": 0.6},
    ]

    teachers = {
        "t1": {"cost": 1.0, "confidence": 0.9, "cap": 5},
        "t2": {"cost": 0.5, "confidence": 0.8, "cap": 5},
    }

    res = allocator.allocate(records, teachers, round_budget=2.0)
    assert "query_plan" in res
    assert len(res["query_plan"]) > 0
