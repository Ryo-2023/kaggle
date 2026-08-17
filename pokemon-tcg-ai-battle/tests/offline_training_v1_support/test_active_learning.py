"""Tests for curriculum scheduling, active learning planning, and uncertainty diagnostics."""

from __future__ import annotations
import pytest
from mage_ptcg.offline_training_v1_support.curriculum import plan_curriculum_batches
from mage_ptcg.offline_training_v1_support.active_learning import plan_active_learning_queries
from mage_ptcg.offline_training_v1_support.uncertainty import diagnose_decision_uncertainty, calculate_predictive_entropy

def test_curriculum_stage_distribution():
    records = [
        {"decision_id": "d1", "split": "train"},
        {"decision_id": "d2", "split": "train"},
        {"decision_id": "d3", "split": "train"},
        {"decision_id": "d4", "split": "val"},  # Must be excluded from curriculum stages
        {"decision_id": "d5", "split": "train", "fallback": True}
    ]

    entropies = {"d1": 1.5, "d2": 1.5, "d3": 0.1} # Two hard cases, one easy case

    # 2 hard / 1 easy = 2.0 (exceeds hard_limit_ratio = 0.3)
    # The curriculum should downgrade one of the hard cases to medium to smooth things out
    batches = plan_curriculum_batches(records, student_entropies=entropies, hard_limit_ratio=0.3)

    assert "d4" not in [x["decision_id"] for stage in batches.values() for x in stage]
    assert len(batches["fallback"]) == 1
    assert len(batches["hard"]) <= 1  # limited
    assert len(batches["medium"]) >= 1 # excess pushed here

def test_active_learning_query_planning():
    records = [
        {"decision_id": "d1", "selection_type": "rare_select", "token": "leak_me"},
        {"decision_id": "d2", "fallback": True, "api_key": "leak_key"},
        {"decision_id": "d3", "context_type": "normal"}
    ]

    uncertainties = {"d1": 0.9, "d2": 0.5, "d3": 0.1}

    res = plan_active_learning_queries(records, uncertainties, query_budget=2)
    assert len(res["queries"]) == 2

    # Check that privacy sensitive fields are not exported
    q1 = res["queries"][0]
    assert "token" not in q1
    assert "api_key" not in q1
    assert q1["decision_id"] == "d1"
    assert "HIGH_UNCERTAINTY" in q1["reasons"]

def test_uncertainty_approximate_diagnostics():
    # 50/50 probability distribution
    probs = [0.5, 0.5]
    entropy = calculate_predictive_entropy(probs)
    # ln(2) approx 0.693
    assert abs(entropy - 0.693147) < 1e-4

    diag = diagnose_decision_uncertainty(probs, teacher_preds=["act_a", "act_b"], is_ood=True)
    assert diag["predictive_entropy"] > 0.6
    assert diag["top2_margin"] == 0.0
    assert diag["teacher_disagreement"] == 1.0
    assert diag["is_ood"] is True
    assert diag["approximate_uncertainty_score"] > 1.0 # boosted by entropy and OOD
