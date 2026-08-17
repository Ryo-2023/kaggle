"""Contracts for the opt-in privacy-safe V4 held-out action trace."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_runner():
    script = Path(__file__).resolve().parents[2] / "scripts" / "measure_v4_checkpoint_strength.py"
    spec = importlib.util.spec_from_file_location("measure_v4_checkpoint_strength_trace", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_public_trace_row_keeps_action_types_and_excludes_private_fields() -> None:
    runner = _load_runner()
    payload = {
        "schema_version": "meta-specialist-runtime-decision-trace-v2",
        "trace_variant": "public-v1-representable",
        "selection_type": 0,
        "selection_context": 8,
        "min_count": 1,
        "max_count": 1,
        "order_semantics": "ordered_sequence",
        "selected_count": 1,
        "complete_action_log_probability": -0.25,
        "public_projection": {
            "selected_public_actions": [
                {
                    "option_type": 13,
                    "semantic_operation": "ATTACK",
                    "public_identity": {"operation": "ATTACK"},
                }
            ]
        },
    }

    row = runner._public_trace_row_v1(
        payload, opponent_id="opponent", seat=1, game_index=3, seed=9003, decision_index=0,
    )

    assert row["opponent_id"] == "opponent"
    assert row["seat"] == 1
    assert row["decision_index"] == 0
    assert row["action_types"] == ["ATTACK"]
    assert "public_projection" not in row
    assert "public_state_digest" not in row
    assert "private_state" not in row


def test_public_trace_row_rejects_unexpected_private_tree() -> None:
    runner = _load_runner()
    payload = {
        "trace_variant": "public-v1-representable",
        "selection_type": 0,
        "selection_context": 8,
        "min_count": 1,
        "max_count": 1,
        "order_semantics": "ordered_sequence",
        "selected_count": 1,
        "complete_action_log_probability": -0.25,
        "public_projection": {
            "selected_public_actions": [{"option_type": 13, "semantic_operation": "ATTACK"}],
            "private_state": {"hand": [1]},
        },
    }

    try:
        runner._public_trace_row_v1(
            payload, opponent_id="opponent", seat=0, game_index=0, seed=9000, decision_index=0,
        )
    except ValueError:
        return
    raise AssertionError("private trace tree must fail closed")


def test_redacted_trace_row_does_not_persist_unrepresentable_projection() -> None:
    runner = _load_runner()
    payload = {
        "trace_variant": "public-v1-representable",
        "selection_type": 0,
        "selection_context": 8,
        "min_count": 1,
        "max_count": 1,
        "order_semantics": "ordered_sequence",
        "selected_count": 1,
        "complete_action_log_probability": -0.25,
        "public_projection": {"private_state": {"hand": [1]}},
    }

    row = runner._redacted_trace_row_v1(
        payload, opponent_id="opponent", seat=0, game_index=0, seed=9000, decision_index=0,
    )

    assert row["trace_variant"] == "public-v1-redacted"
    assert row["action_types"] == []
    assert row["complete_action_log_probability"] == -0.25
    assert "public_projection" not in row
