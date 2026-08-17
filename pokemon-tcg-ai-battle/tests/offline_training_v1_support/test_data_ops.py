"""Tests for data quality, leakage audit, drift detection, and repair planning."""

from __future__ import annotations
import pytest
from mage_ptcg.offline_training_v1_support.data_quality import profile_dataset
from mage_ptcg.offline_training_v1_support.drift import detect_categorical_drift
from mage_ptcg.offline_training_v1_support.leakage_audit import audit_split_leakage
from mage_ptcg.offline_training_v1_support.data_repair import DataRepairPlanner

def test_data_quality_profiler():
    records = [
        {"decision_id": "dec-1", "episode_id": "ep-1", "chosen_action": "a", "constant_val": 42},
        {"decision_id": "dec-2", "episode_id": "ep-1", "chosen_action": "b", "constant_val": 42},
        {"decision_id": "dec-1", "episode_id": "ep-1", "chosen_action": "c", "constant_val": 42}, # Conflicting chosen_action for dec-1
    ]
    res = profile_dataset(records)
    assert res["record_count"] == 3
    assert res["episode_count"] == 1
    assert res["decision_count"] == 2
    assert "constant_val" in res["constant_fields"]
    # Because of conflicting action for dec-1: 1 out of 2 decisions conflict (50% rate)
    assert res["conflicting_label_rate"] == 0.5
    assert res["status"] == "FAIL"

def test_distribution_drift():
    dataset_a = [{"color": "red"} for _ in range(80)] + [{"color": "blue"} for _ in range(20)]
    # Dataset B has a distinct distribution
    dataset_b = [{"color": "red"} for _ in range(40)] + [{"color": "blue"} for _ in range(60)]

    res = detect_categorical_drift(dataset_a, dataset_b, "color", tvd_threshold=0.1, psi_threshold=0.2)
    assert res["tvd"] > 0.1
    assert res["psi"] > 0.2
    assert res["drift_detected"] is True

def test_data_leakage_audit():
    train_records = [
        {"decision_id": "dec-1", "episode_id": "ep-1"},
        {"decision_id": "dec-2", "episode_id": "ep-2"}
    ]
    val_records = [
        {"decision_id": "dec-2", "episode_id": "ep-2"}, # Leak! Shared dec-2 and ep-2
        {"decision_id": "dec-3", "episode_id": "ep-3"}
    ]
    res = audit_split_leakage(train_records, val_records)
    assert res["leakage_detected"] is True
    assert "ep-2" in res["episode_overlaps"]["train_val"]
    assert "dec-2" in res["decision_overlaps"]["train_val"]
    assert any("Episode overlap" in x for x in res["issues"])

def test_data_repair_planning_and_simulation():
    profile = {
        "duplicate_rate": 0.15,
        "conflicting_label_rate": 0.08
    }
    leakage = {
        "leakage_detected": True
    }

    planner = DataRepairPlanner(profile, leakage)
    plan = planner.generate_plan()

    assert len(plan) == 3
    issues = [item["issue"] for item in plan]
    assert "DUPLICATE_RECORDS" in issues
    assert "CONFLICTING_LABELS" in issues
    assert "SPLIT_LEAKAGE" in issues

    # Simulate repair
    records = [
        {"decision_id": "dec-1", "chosen_action": "a"},
        {"decision_id": "dec-1", "chosen_action": "a"}, # duplicate record
        {"decision_id": "dec-2", "chosen_action": "b"}
    ]
    repaired = planner.simulate_repair(records)
    assert len(repaired) == 2
