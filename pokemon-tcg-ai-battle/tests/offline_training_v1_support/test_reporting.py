"""Tests for reporting, cards, retention planning, and API docs."""

from __future__ import annotations
import pytest
from mage_ptcg.offline_training_v1_support.reporting import generate_markdown_report, generate_html_report
from mage_ptcg.offline_training_v1_support.cards import generate_dataset_card, generate_model_card
from mage_ptcg.offline_training_v1_support.retention import RetentionPlanner
from mage_ptcg.offline_training_v1_support.api_docs import generate_api_reference

def test_static_reports():
    metrics = {
        "total_games": 100,
        "overall_win_rate": 0.55,
        "crash_count": 0,
        "timeout_count": 1,
        "legal_action_rate": 1.0,
        "fallback_rate": 0.0
    }

    md = generate_markdown_report(metrics)
    assert "# Evaluation Run Summary Report" in md
    assert "55.00%" in md

    html_repr = generate_html_report(metrics)
    assert "<title>Evaluation Summary Dashboard</title>" in html_repr
    assert "55.00%" in html_repr

def test_metadata_cards():
    dataset_card = generate_dataset_card("abc-digest-123", 500)
    assert "# Dataset Card" in dataset_card
    assert "abc-digest-123" in dataset_card

    model_card = generate_model_card("model-v5", "transformer", {"loss": 0.05})
    assert "# Model Card" in model_card
    assert "model-v5" in model_card
    assert "loss**: 0.05" in model_card

def test_retention_planning():
    inventory = [
        {"path": "/data/dataset_run1.gz", "size_bytes": 1024, "protected": False},
        {"path": "/models/checkpoint_epoch10.pth", "size_bytes": 2048, "protected": False},
        {"path": "/tmp/interim_results.json", "size_bytes": 100, "protected": False},
        {"path": "/evidence/verification.md", "size_bytes": 50, "protected": True}
    ]

    planner = RetentionPlanner(inventory)
    plan = planner.generate_cleanup_plan()

    assert len(plan) == 4

    # Check classifications
    assert plan[0]["category"] == "dataset"
    assert plan[0]["recommended_action"] == "ARCHIVE"

    assert plan[1]["category"] == "checkpoint"
    assert plan[1]["recommended_action"] == "REBUILDABLE"

    assert plan[2]["category"] == "temporary"
    assert plan[2]["recommended_action"] == "ELIGIBLE_FOR_DELETION"

    assert plan[3]["category"] == "evidence"
    assert plan[3]["recommended_action"] == "PROTECTED"

def test_api_reference_generation():
    doc = generate_api_reference()
    assert "# Support Platform API Reference Documentation" in doc
    assert "## Module `contracts`" in doc
    assert "### `canonical_json`" in doc
