"""Contracts for the sparse, public-only V4 action diagnostic."""

from __future__ import annotations

import pytest


def _load_module():
    import importlib

    return importlib.import_module("scripts.build_v4_public_trace_action_table_v1")


def test_sparse_public_action_table_is_not_screen_ready() -> None:
    module = _load_module()
    result = module.build_public_action_table_v1(
        summary={
            "schema_version": "meta-specialist-v4-public-trace-meta-train-v1",
            "requested_games": 1,
            "faults": 0,
            "native_action_labels_saved": False,
            "private_fields_saved": False,
        },
        ledger_rows=[{"game_id": "g0", "outcome": "win"}],
        trace_rows=[
            {
                "game_id": "g0", "outcome": "win", "action_types": ["SKILL"],
                "trace_variant": "public-v1-representable",
            }
        ],
    )
    assert result["usable_signal"] is False
    assert "insufficient_competing_action_types" in result["reasons"]
    assert result["action_events"] == 1


def test_public_action_table_rejects_private_trace_fields() -> None:
    module = _load_module()
    with pytest.raises(ValueError, match="forbidden"):
        module.build_public_action_table_v1(
            summary={
                "schema_version": "meta-specialist-v4-public-trace-meta-train-v1",
                "requested_games": 1,
                "faults": 0,
                "native_action_labels_saved": False,
                "private_fields_saved": False,
            },
            ledger_rows=[{"game_id": "g0", "outcome": "win"}],
            trace_rows=[
                {
                    "game_id": "g0", "outcome": "win", "action_types": ["SKILL"],
                    "private_state": {"x": 1},
                }
            ],
        )
