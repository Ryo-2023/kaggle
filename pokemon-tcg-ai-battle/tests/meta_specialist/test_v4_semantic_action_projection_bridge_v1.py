"""TDD contracts for the V4 minimal semantic-action projection bridge."""

from __future__ import annotations

import hashlib
import json

import pytest

from mage_ptcg.meta_specialist.v4_semantic_action_projection_bridge_v1 import (
    PROJECTION_SCHEMA_V1,
    aggregate_projection_rows_v1,
    project_v4_decision_v1,
    reload_projection_row_v1,
)


def _action(operation: str, option_type: int) -> dict[str, object]:
    return {
        "action_key_schema_version": 2,
        "context": 0,
        "option_type": option_type,
        "public_identity": {
            "operation": operation,
            "fields": {"attackId": 1} if operation == "ATTACK" else {},
            "private_source_redacted": True,
        },
        "selection_type": 0,
        "semantic_operation": operation,
    }


def _trace() -> dict[str, object]:
    return {
        "action_keys": [_action("ATTACK", 13), _action("END", 14)],
        "actor": 0,
        "belief_summary": None,
        "metadata": {
            "public_state_digest": "a" * 64,
            "public_action_set_digest": "b" * 64,
            "schema_version": 1,
        },
        "public_state": {"turn": 2},
        "visible_history": [],
        "trace_digest": "c" * 64,
    }


def test_projection_keeps_only_public_semantic_keys_and_boundary() -> None:
    row = project_v4_decision_v1(
        public_trace=_trace(), chosen_option_indices=(1,), game_id="g-1", episode_id="e-1",
        outcome="loss", seat=0, opponent_id="opponent", seed=14910000,
        selection_type=0, selection_context=0, min_count=1, max_count=1,
    )

    assert row["schema_version"] == PROJECTION_SCHEMA_V1
    assert row["public_state_digest"] == "a" * 64
    assert len(row["legal_semantic_action_keys"]) == 2
    assert row["chosen_semantic_action_keys"][0]["semantic_operation"] == "END"
    assert row["boundary"] == {
        "min_count": 1, "max_count": 1, "selected_count": 1,
        "stop_available": False, "complete": True,
    }
    encoded = json.dumps(row, sort_keys=True)
    assert "option_index" not in encoded
    assert "private_state" not in encoded
    assert "action_key_digest" not in encoded


def test_projection_rejects_illegal_choice_and_private_source() -> None:
    with pytest.raises(ValueError, match="legal"):
        project_v4_decision_v1(
            public_trace=_trace(), chosen_option_indices=(2,), game_id="g", episode_id="e",
            outcome="win", seat=0, opponent_id="opponent", seed=1,
            selection_type=0, selection_context=0, min_count=1, max_count=1,
        )

    tampered = _trace()
    tampered["action_keys"] = [{"private_state": {"hand": [1]}}]
    with pytest.raises(ValueError, match="private"):
        project_v4_decision_v1(
            public_trace=tampered, chosen_option_indices=(0,), game_id="g", episode_id="e",
            outcome="win", seat=0, opponent_id="opponent", seed=1,
            selection_type=0, selection_context=0, min_count=1, max_count=1,
        )


def test_projection_roundtrip_rederives_sha_and_coverage_gate() -> None:
    row = project_v4_decision_v1(
        public_trace=_trace(), chosen_option_indices=(0,), game_id="g-1", episode_id="e-1",
        outcome="win", seat=0, opponent_id="opponent", seed=1,
        selection_type=0, selection_context=0, min_count=1, max_count=1,
    )
    reloaded = reload_projection_row_v1(row)
    assert reloaded == row
    assert reloaded["row_sha256"] == hashlib.sha256(
        ("v4-semantic-action-projection-v1\0" + json.dumps(
            {key: value for key, value in row.items() if key != "row_sha256"},
            sort_keys=True, separators=(",", ":"),
        )).encode()
    ).hexdigest()
    summary = aggregate_projection_rows_v1([row])
    assert summary["complete_rows"] == 1
    assert summary["distinct_semantic_operations"] == 1
    assert summary["usable_signal"] is False
