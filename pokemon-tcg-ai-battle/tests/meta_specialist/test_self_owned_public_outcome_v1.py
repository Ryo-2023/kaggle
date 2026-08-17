from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.self_owned_public_outcome_v1 import (
    SelfOwnedPublicOutcomeError,
    build_bounded_action_overlay_v1,
    build_overlay_agent_v1,
    build_public_outcome_rows_v1,
    load_overlay_table_v1,
    save_overlay_table_v1,
)


SHA = "a" * 64


def _records() -> list[dict[str, object]]:
    return [
        {
            "game_id": "g0",
            "candidate_side": 0,
            "outcome": "win",
            "opponent_id": "native-a",
            "subject_policy_sha256": SHA,
            "subject_deck_sha256": "b" * 64,
            "actions": [
                {"step_index": 1, "seat": 0, "action_type": "ATTACK", "state_digest": "c" * 64, "action_digest": "d" * 64},
                {"step_index": 2, "seat": 0, "action_type": "PLAY", "state_digest": "e" * 64, "action_digest": "f" * 64},
            ],
        },
        {
            "game_id": "g1",
            "candidate_side": 0,
            "outcome": "loss",
            "opponent_id": "native-b",
            "subject_policy_sha256": SHA,
            "subject_deck_sha256": "b" * 64,
            "actions": [
                {"step_index": 1, "seat": 0, "action_type": "PLAY", "state_digest": "1" * 64, "action_digest": "2" * 64},
                {"step_index": 2, "seat": 0, "action_type": "PLAY", "state_digest": "3" * 64, "action_digest": "4" * 64},
            ],
        },
    ]


def test_public_outcome_rows_keep_only_subject_public_action_digests() -> None:
    rows = build_public_outcome_rows_v1(
        game_id="g0",
        subject_side=0,
        outcome="win",
        opponent_id="native-a",
        subject_policy_sha256=SHA,
        subject_deck_sha256="b" * 64,
        events=[
            {
                "step_index": 3,
                "seat_direction": "SEAT_0",
                "public_payload": {"action": {"option_type_name": "ATTACK"}},
            },
            {
                "step_index": 4,
                "seat_direction": "SEAT_1",
                "public_payload": {"action": {"option_type_name": "PLAY"}},
            },
        ],
    )
    assert rows == [
        {
            "game_id": "g0",
            "step_index": 3,
            "seat": 0,
            "action_type": "ATTACK",
            "outcome": "win",
            "opponent_id": "native-a",
            "state_digest": rows[0]["state_digest"],
            "action_digest": rows[0]["action_digest"],
            "subject_policy_sha256": SHA,
            "subject_deck_sha256": "b" * 64,
        }
    ]
    assert len(rows[0]["state_digest"]) == 64
    assert len(rows[0]["action_digest"]) == 64


def test_bounded_table_requires_real_source_identity_and_caps_delta() -> None:
    table = build_bounded_action_overlay_v1(
        _records(),
        source_policy_sha256=SHA,
        source_deck_sha256="b" * 64,
        max_abs_delta=120.0,
        minimum_observations=1,
    )
    assert table["schema_version"] == "self-owned-public-action-outcome-table-v1"
    assert table["source_policy_sha256"] == SHA
    assert all(abs(float(row["delta"])) <= 120.0 for row in table["action_types"].values())
    assert table["authority"] == {
        "training_authority": False,
        "promotion_authority": False,
        "submission_authority": False,
    }
    with pytest.raises(SelfOwnedPublicOutcomeError, match="source policy"):
        build_bounded_action_overlay_v1(
            _records(), source_policy_sha256="9" * 64, source_deck_sha256="b" * 64
        )


def test_common24_source_provenance_is_bound_and_roundtrips() -> None:
    provenance = {
        "schema_version": "self-owned-public-rollout-source-v1",
        "common24_ids": [f"opponent-{i:02d}" for i in range(24)],
        "games_per_cell": 2,
        "base_seed": 14900000,
        "evaluator_sha256": "1" * 64,
        "rollout_manifest_sha256": "2" * 64,
        "record_count": 2,
        "engine_seed_support": "ENGINE_SEED_UNSUPPORTED",
        "authority": {
            "training_authority": False,
            "promotion_authority": False,
            "submission_authority": False,
        },
    }
    table = build_bounded_action_overlay_v1(
        _records(), source_policy_sha256=SHA, source_deck_sha256="b" * 64,
        max_abs_delta=120.0, minimum_observations=1,
        source_provenance=provenance,
    )
    assert table["source_provenance"] == provenance


def test_overlay_is_native_first_legal_and_hash_bound(tmp_path: Path) -> None:
    table = build_bounded_action_overlay_v1(
        _records(), source_policy_sha256=SHA, source_deck_sha256="b" * 64,
        max_abs_delta=300.0, minimum_observations=1,
    )
    path = tmp_path / "table.json"
    save_overlay_table_v1(path, table)
    loaded = load_overlay_table_v1(path)
    agent = build_overlay_agent_v1(
        deck=[1] * 60, table=loaded, baseline_policy_sha256=SHA,
        candidate_config_sha256=loaded["config_sha256"],
    )
    observation = {
        "select": {
            "type": 0,
            "minCount": 1,
            "maxCount": 1,
            "option": [{"type": 7}, {"type": 13}],
        }
    }
    selected = agent(observation)
    assert selected in ([0], [1])
    telemetry = agent.telemetry()
    assert telemetry["native_calls"] == 1
    assert telemetry["candidate_config_sha256"] == loaded["config_sha256"]
    malformed = dict(loaded)
    malformed["config_sha256"] = "0" * 64
    with pytest.raises(SelfOwnedPublicOutcomeError, match="config SHA"):
        build_overlay_agent_v1(
            deck=[1] * 60, table=malformed, baseline_policy_sha256=SHA,
            candidate_config_sha256=loaded["config_sha256"],
        )


def test_overlay_rejects_private_or_unknown_action_rows() -> None:
    bad = _records()
    bad[0]["actions"] = [{"action_type": "ATTACK", "private_state": {"hand": [1]}}]
    with pytest.raises(SelfOwnedPublicOutcomeError, match="public"):
        build_bounded_action_overlay_v1(
            bad, source_policy_sha256=SHA, source_deck_sha256="b" * 64
        )
