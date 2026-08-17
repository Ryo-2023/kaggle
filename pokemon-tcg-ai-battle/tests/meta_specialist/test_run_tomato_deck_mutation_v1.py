from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_prepare_tomato_manifest_is_candidate_only_and_hash_bound(tmp_path: Path) -> None:
    from scripts.run_tomato_deck_mutation_v1 import prepare_tomato_mutation_manifest_v1

    manifest_path = prepare_tomato_mutation_manifest_v1(
        output_root=tmp_path,
        source_root=ROOT,
        seed=20260813,
    )

    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["subject_id"] == "tomatomato_archaludon"
    assert payload["candidate_status"] == "candidate_only"
    assert payload["research_only"] is True
    assert payload["authority"] == {
        "execute_allowed": False,
        "promotion_allowed": False,
        "submission_allowed": False,
        "training_allowed": False,
    }
    assert len(payload["candidates"]) == 8
    assert {item["swap_count"] for item in payload["candidates"]} == {1, 2}
    assert len({item["candidate_id"] for item in payload["candidates"]}) == 8
    assert all(item["deck_csv_path"] for item in payload["candidates"])
    assert all(item["deck_csv_sha256"] for item in payload["candidates"])
    assert all(item["deck_multiset_sha256"] != payload["parent"]["deck_multiset_sha256"] for item in payload["candidates"])


def test_screen_cells_cover_24_opponents_both_seats_and_parent_arm(tmp_path: Path) -> None:
    from scripts.run_tomato_deck_mutation_v1 import (
        build_tomato_mutation_screen_games_v1,
        prepare_tomato_mutation_manifest_v1,
    )

    manifest_path = prepare_tomato_mutation_manifest_v1(
        output_root=tmp_path,
        source_root=ROOT,
        seed=20260813,
    )
    games = build_tomato_mutation_screen_games_v1(
        manifest_path=manifest_path,
        source_root=ROOT,
        games_per_opponent_seat=1,
    )

    # Eight candidate arms plus the unmodified native parent: 24 opponents,
    # two seats, one repetition per cell.
    assert len(games) == 9 * 24 * 2
    arms = {str(game.metadata["comparison_arm"]) for game in games}
    assert arms == {"parent_native", *(f"candidate:{index}" for index in range(8))}
    for arm in arms:
        rows = [game for game in games if game.metadata["comparison_arm"] == arm]
        assert len(rows) == 48
        assert {game.seat for game in rows} == {0, 1}
        assert len({game.opponent_id for game in rows}) == 24
        assert all(game.metadata["research_only"] is True for game in rows)
        assert all(game.metadata["promotion_authority"] is False for game in rows)
        assert all(game.metadata["training_authority"] is False for game in rows)
        assert all(game.metadata["submission_authority"] is False for game in rows)


def test_legality_cells_use_the_native_tomato_policy_not_rule_agent(tmp_path: Path) -> None:
    from scripts.run_tomato_deck_mutation_v1 import (
        build_tomato_mutation_legality_games_v1,
        prepare_tomato_mutation_manifest_v1,
    )

    manifest_path = prepare_tomato_mutation_manifest_v1(
        output_root=tmp_path,
        source_root=ROOT,
        seed=20260813,
    )
    games = build_tomato_mutation_legality_games_v1(
        manifest_path=manifest_path,
        source_root=ROOT,
    )

    # Every arm is checked against the stable random reference from both seats.
    # This tests the same native policy/deck boundary that the screen will use.
    assert len(games) == 9 * 2
    assert {game.opponent_id for game in games} == {"official_random"}
    assert {game.seat for game in games} == {0, 1}
    assert {game.runner_ref for game in games} == {
        "scripts.run_native_policy_candidate_pilot_v1:run_native_candidate_game_v1"
    }


def test_confirmation_cells_keep_only_positive_candidate_and_parent_at_384_each(tmp_path: Path) -> None:
    from scripts.run_tomato_deck_mutation_v1 import (
        build_tomato_mutation_confirmation_games_v1,
        prepare_tomato_mutation_manifest_v1,
    )

    manifest_path = prepare_tomato_mutation_manifest_v1(
        output_root=tmp_path,
        source_root=ROOT,
        seed=20260813,
    )
    games = build_tomato_mutation_confirmation_games_v1(
        manifest_path=manifest_path,
        source_root=ROOT,
        candidate_index=7,
    )

    assert len(games) == 2 * 24 * 2 * 8
    assert {game.metadata["comparison_arm"] for game in games} == {
        "parent_native", "candidate:7"
    }
    assert {game.opponent_id for game in games} == set(json.loads(
        (ROOT / "configs/meta_specialist/performance_first_broad_pool_v1.json").read_text()
    )["opponent_ids"])
