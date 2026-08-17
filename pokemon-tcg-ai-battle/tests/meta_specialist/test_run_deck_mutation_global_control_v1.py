from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "runs/final-sprint-autonomous/deck-mutation-plamen-v1/candidates.json"
REFERENCE = ROOT / "configs/meta_specialist/performance_first_broad_pool_v1.json"


def test_global_control_is_paired_and_research_only() -> None:
    from scripts.run_deck_mutation_global_control_v1 import build_global_control_games_v1

    games = build_global_control_games_v1(
        manifest_path=MANIFEST,
        candidate_id="3f64513bf1c069b7e14c889b2c94150f7e0dd58697e9004c937871344668719f",
        reference_config=REFERENCE,
        pool_root=ROOT / "opponents",
        games_per_opponent_seat=1,
        base_seed=14_400_000,
    )
    assert len(games) == 96
    assert len({game.game_id for game in games}) == len(games)
    arms = {game.metadata["comparison_arm"] for game in games}
    assert arms == {"candidate", "tomato_native"}
    candidate = [game for game in games if game.metadata["comparison_arm"] == "candidate"]
    tomato = [game for game in games if game.metadata["comparison_arm"] == "tomato_native"]
    assert [game.seed for game in candidate] == [game.seed for game in tomato]
    assert {game.metadata["common_reference_count"] for game in games} == {24}
    assert all(game.metadata["research_only"] is True for game in games)
    assert all(game.metadata["promotion_authority"] is False for game in games)
    assert all(game.metadata["training_authority"] is False for game in games)
    assert all(game.metadata["submission_authority"] is False for game in games)


def test_tomato_policy_can_be_bound_to_mutation_deck() -> None:
    import hashlib

    from scripts.run_deck_mutation_global_control_v1 import build_global_control_games_v1

    games = build_global_control_games_v1(
        manifest_path=MANIFEST,
        candidate_id="3f64513bf1c069b7e14c889b2c94150f7e0dd58697e9004c937871344668719f",
        reference_config=REFERENCE,
        pool_root=ROOT / "opponents",
        games_per_opponent_seat=1,
        base_seed=14_500_000,
        candidate_policy_asset_id="tomatomato_archaludon",
    )
    candidate = [game for game in games if game.metadata["comparison_arm"] == "candidate"]
    tomato = [game for game in games if game.metadata["comparison_arm"] == "tomato_native"]
    assert candidate and tomato
    tomato_policy_sha = hashlib.sha256(
        (ROOT / "opponents/tomatomato_archaludon/main.py").read_bytes()
    ).hexdigest()
    assert {game.metadata["candidate_policy_asset_id"] for game in candidate} == {
        "tomatomato_archaludon"
    }
    assert {game.metadata["candidate_policy_sha256"] for game in candidate} == {
        tomato_policy_sha
    }
    assert {game.metadata["candidate_deck_sha256"] for game in candidate} != {
        game.metadata["candidate_deck_sha256"] for game in tomato
    }
    assert [game.seed for game in candidate] == [game.seed for game in tomato]
