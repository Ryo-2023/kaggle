from __future__ import annotations

import pytest

from mage_ptcg.meta_specialist.self_owned_public_advantage_v1 import (
    SelfOwnedPublicAdvantageError,
    build_state_action_advantage_table_v1,
    extract_public_state_features_v1,
)
from scripts.build_self_owned_public_advantage_v1 import (
    AdvantageBundleError,
    materialize_state_action_bundle_v1,
)


def _event(*, action_type: str = "PLAY") -> dict[str, object]:
    return {
        "event_type": "PUBLIC_ACTION",
        "schema_version": "o6-public-trajectory-v1",
        "step_index": 22,
        "seat_direction": "SEAT_0",
        "public_payload": {
            "action": {"option_type": 7, "option_type_name": action_type},
            "board": {
                "energy_attached": True,
                "retreated": False,
                "stadium": [],
                "stadium_played": False,
                "supporter_played": False,
            },
            "players": [
                {
                    "active": [{"current_hp": 120, "max_hp": 180, "attached_energy_count": 2}],
                    "bench": [], "bench_max": 5, "deck_count": 40, "hand_count": 5, "prize_count": 4,
                    "discard": [], "status": {"asleep": False, "burned": False, "confused": False, "paralyzed": False, "poisoned": False},
                },
                {
                    "active": [{"current_hp": 60, "max_hp": 160, "attached_energy_count": 1}],
                    "bench": [], "bench_max": 5, "deck_count": 35, "hand_count": 3, "prize_count": 5,
                    "discard": [], "status": {"asleep": False, "burned": False, "confused": False, "paralyzed": False, "poisoned": False},
                },
            ],
            "result": -1,
        },
    }


def _provenance() -> dict[str, object]:
    return {
        "schema_version": "self-owned-public-state-action-source-v1",
        "common24_ids": [f"opponent-{i:02d}" for i in range(24)],
        "games_per_cell": 2,
        "base_seed": 14900000,
        "evaluator_sha256": "1" * 64,
        "rollout_manifest_sha256": "2" * 64,
        "records_sha256": "3" * 64,
        "record_count": 96,
        "engine_seed_support": "ENGINE_SEED_UNSUPPORTED",
        "authority": {"training_authority": False, "promotion_authority": False, "submission_authority": False},
    }


def _example(*, state: str = "state-a", action: str = "PLAY", outcome: str = "win") -> dict[str, object]:
    return {
        "game_id": "g0",
        "opponent_id": "native-a",
        "outcome": outcome,
        "action_type": action,
        "state_bucket": state,
        "state_features": {"phase_bucket": 1, "own_prize_bucket": 4},
        "state_digest": "a" * 64,
        "action_digest": "b" * 64,
    }


def test_extract_features_is_public_bucketed_and_does_not_forward_card_identity() -> None:
    result = extract_public_state_features_v1(_event(), subject_side=0, action_ordinal=9)
    assert result["phase_bucket"] == 1
    assert result["action_ordinal_bucket"] == 1
    assert result["own_active_hp_bucket"] == 2
    assert result["opp_active_hp_bucket"] == 1
    assert result["own_hand_bucket"] == 1
    assert "card_id" not in result
    assert "hand" not in result


def test_extract_features_rejects_private_or_unknown_public_fields() -> None:
    event = _event()
    event["public_payload"]["private_state"] = {"hand": [1]}  # type: ignore[index]
    with pytest.raises(SelfOwnedPublicAdvantageError, match="private"):
        extract_public_state_features_v1(event, subject_side=0, action_ordinal=0)
    event = _event()
    event["public_payload"]["board"]["new_field"] = 1  # type: ignore[index]
    with pytest.raises(SelfOwnedPublicAdvantageError, match="unknown"):
        extract_public_state_features_v1(event, subject_side=0, action_ordinal=0)


def test_state_action_table_computes_within_state_advantage_and_source_binding() -> None:
    examples = [_example(action="PLAY", outcome="win") for _ in range(4)]
    examples += [_example(action="ATTACK", outcome="loss") for _ in range(4)]
    table = build_state_action_advantage_table_v1(examples, source_provenance=_provenance(), min_support=2)
    assert table["schema_version"] == "self-owned-public-state-action-advantage-v1"
    assert table["source_provenance"] == _provenance()
    actions = table["state_buckets"]["state-a"]["actions"]
    assert actions["PLAY"]["advantage"] > 0
    assert actions["ATTACK"]["advantage"] < 0


def test_sparse_or_all_negative_signal_is_not_screen_ready() -> None:
    examples = [_example(action="PLAY", outcome="loss") for _ in range(3)]
    table = build_state_action_advantage_table_v1(examples, source_provenance=_provenance(), min_support=2)
    assert table["quality_gate"]["ready_for_candidate_screen"] is False
    assert "sparse" in table["quality_gate"]["reasons"]


def test_bundle_materializer_is_atomic_and_rejects_conflicting_existing_bytes(tmp_path) -> None:
    table = build_state_action_advantage_table_v1(
        [_example(action="PLAY", outcome="win") for _ in range(4)] + [_example(action="ATTACK", outcome="loss") for _ in range(4)],
        source_provenance=_provenance(), min_support=2,
    )
    output = tmp_path / "bundle"
    result = materialize_state_action_bundle_v1(output_dir=output, table=table, source_provenance=_provenance())
    assert result["ready_for_candidate_screen"] is False
    assert result["authority"] == {"training_authority": False, "promotion_authority": False, "submission_authority": False}
    (output / "bundle-manifest.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(AdvantageBundleError, match="overwrite"):
        materialize_state_action_bundle_v1(output_dir=output, table=table, source_provenance=_provenance())
