"""Tests for teacher analysis and label consensus engine."""

from __future__ import annotations
import pytest
from mage_ptcg.offline_training_v1_support.teacher_analysis import analyze_teacher_reliability
from mage_ptcg.offline_training_v1_support.label_consensus import compute_label_consensus

def test_teacher_reliability_analysis():
    outputs = [
        {"teacher_id": "t1", "decision_id": "d1", "chosen_action": "act_a"},
        {"teacher_id": "t1", "decision_id": "d2", "chosen_action": "act_b"},
        {"teacher_id": "t2", "decision_id": "d1", "chosen_action": "act_a"},
        {"teacher_id": "t2", "decision_id": "d2", "chosen_action": "act_a", "fallback": True}, # fallback
        {"teacher_id": "t1", "decision_id": "d3", "failed": True} # failed
    ]

    true_labels = {
        "d1": "act_a",
        "d2": "act_b"
    }

    res = analyze_teacher_reliability(outputs, true_labels)
    assert res["total_outputs"] == 5
    assert res["failure_rate"] == 0.2
    assert res["fallback_rate"] == 0.2

    # Pairwise agreement between t1 and t2 on d1 & d2
    # d1: t1 -> act_a, t2 -> act_a (agree)
    # d2: t1 -> act_b, t2 -> act_a (disagree)
    # Total pairs: 2, matches: 1 -> 50%
    assert res["pairwise_agreement"] == 0.5

    # t1 correct: d1(yes), d2(yes). accuracy = 100%
    # t2 correct: d1(yes), d2(no). accuracy = 50%
    assert res["teacher_accuracies"]["t1"] == 1.0
    assert res["teacher_accuracies"]["t2"] == 0.5

def test_label_consensus_tie_break():
    # 50/50 split between act_b and act_a.
    # Stable ActionKey tie-break must choose act_a (lexicographical)
    predictions = [
        {"teacher_id": "t1", "chosen_action": "act_b"},
        {"teacher_id": "t2", "chosen_action": "act_a"}
    ]

    res = compute_label_consensus("dec-100", predictions, method="majority")
    assert res["consensus_action"] == "act_a"
    assert res["confidence"] == 0.5
    assert res["status"] == "QUARANTINE" # because confidence < 0.6

def test_label_consensus_weighted():
    # Weighted consensus should prioritize t1's decision
    predictions = [
        {"teacher_id": "t1", "chosen_action": "act_b", "confidence": 0.8},
        {"teacher_id": "t2", "chosen_action": "act_a", "confidence": 0.9}
    ]
    teacher_weights = {"t1": 2.0, "t2": 1.0}

    # t1 score: 0.8 * 2.0 = 1.6
    # t2 score: 0.9 * 1.0 = 0.9
    res = compute_label_consensus("dec-101", predictions, teacher_weights, method="reliability_weighted")
    assert res["consensus_action"] == "act_b"
    assert res["status"] == "SUCCESS" # 1.6 / 2.5 = 64% (>= 60%)
