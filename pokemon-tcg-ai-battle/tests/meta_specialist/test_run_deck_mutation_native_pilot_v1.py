from __future__ import annotations

import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "runs/final-sprint-autonomous/deck-mutation-plamen-v1/candidates.json"
REFERENCE = ROOT / "configs/meta_specialist/performance_first_broad_pool_v1.json"


def test_candidate_manifest_is_closed_and_native_policy_is_fixed() -> None:
    from scripts.run_deck_mutation_native_pilot_v1 import (
        load_candidate_manifest_v1,
    )

    loaded = load_candidate_manifest_v1(MANIFEST)
    assert loaded.subject_id == "plamen06_steel"
    assert len(loaded.candidates) == 8
    assert loaded.parent_policy_sha256 == loaded.pool_policy_sha256
    assert all(candidate.authority == {
        "promotion_allowed": False,
        "training_allowed": False,
        "submission_allowed": False,
    } for candidate in loaded.candidates)
    assert all(candidate.deck_csv_path.is_file() for candidate in loaded.candidates)


def test_builder_expands_eight_candidates_to_736_balanced_native_games() -> None:
    from scripts.run_deck_mutation_native_pilot_v1 import (
        build_native_candidate_games_v1,
        load_candidate_manifest_v1,
    )

    loaded = load_candidate_manifest_v1(MANIFEST)
    games = build_native_candidate_games_v1(
        loaded,
        reference_config_path=REFERENCE,
        pool_root=ROOT / "opponents",
        games_per_opponent_seat=2,
    )
    assert len(games) == 736
    assert len({game.metadata["candidate_id"] for game in games}) == 8
    assert all(game.metadata["promotion_authority"] is False for game in games)
    assert all(game.metadata["training_authority"] is False for game in games)
    assert all(game.metadata["submission_authority"] is False for game in games)
    for candidate_id in {game.metadata["candidate_id"] for game in games}:
        candidate_games = [game for game in games if game.metadata["candidate_id"] == candidate_id]
        assert len(candidate_games) == 92
        assert {game.opponent_id for game in candidate_games} == {
            item for item in json.loads(REFERENCE.read_text(encoding="utf-8"))["opponent_ids"]
            if item != "plamen06_steel"
        }
        assert {game.seat for game in candidate_games} == {0, 1}
        assert {game.metadata["native_policy_sha256"] for game in candidate_games} == {
            loaded.parent_policy_sha256
        }


def test_manifest_true_authority_fails_closed(tmp_path: Path) -> None:
    from scripts.run_deck_mutation_native_pilot_v1 import (
        DeckMutationNativePilotError,
        load_candidate_manifest_v1,
    )

    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    payload["authority"]["training_allowed"] = True
    bad = tmp_path / "candidates.json"
    bad.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(DeckMutationNativePilotError, match="authority"):
        load_candidate_manifest_v1(bad)


def test_candidate_summary_is_grouped_and_retains_fault_denominator() -> None:
    from scripts.run_deck_mutation_native_pilot_v1 import summarize_native_candidate_rows_v1

    rows = [
        {"outcome": "win", "raw_status": "DONE", "seat": 0, "opponent_id": "a",
         "metadata": {"candidate_id": "c1", "candidate_deck_sha256": "d" * 64}},
        {"outcome": "fault", "raw_status": None, "seat": 1, "opponent_id": "a",
         "metadata": {"candidate_id": "c1", "candidate_deck_sha256": "d" * 64}},
    ]
    summary = summarize_native_candidate_rows_v1(rows)
    assert summary["c1"]["requested_games"] == 2
    assert summary["c1"]["completed_games"] == 1
    assert summary["c1"]["faults"] == 1
    assert summary["c1"]["score_denominator_games"] == 2
