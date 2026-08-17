"""TDD contracts for the performance-first root submission arena."""

from pathlib import Path


def test_build_root_arena_games_binds_current_deck_and_pool_identity() -> None:
    from scripts.run_performance_first_arena_v1 import build_root_arena_games

    games = build_root_arena_games(
        opponent_ids=("public_archaludon_cinderace_r7",),
        games_per_seat=1,
        base_seed=220000,
    )
    assert len(games) == 2
    assert {game.seat for game in games} == {0, 1}
    assert all(game.policy_id == "rule-v0-root-deck" for game in games)
    assert all(Path(game.subject_deck_path).name == "deck.csv" for game in games)
    assert all(game.runner_ref.endswith(":run_root_pool_game_v1") for game in games)


def test_build_root_arena_games_accepts_explicit_subject_deck() -> None:
    from scripts.run_performance_first_arena_v1 import build_root_arena_games

    subject = Path("opponents/public_archaludon_cinderace_r7/deck.csv").resolve()
    games = build_root_arena_games(
        opponent_ids=("public_archaludon_cinderace_r7",),
        games_per_seat=1,
        base_seed=220000,
        subject_deck=subject,
    )
    assert len(games) == 2
    assert all(Path(game.subject_deck_path) == subject for game in games)
    assert all(game.deck_sha256 != "2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19" for game in games)


def test_root_arena_runner_reference_is_importable() -> None:
    from scripts.parallel_cabt_evaluator_v1 import _resolve_runner_v1
    from scripts.run_performance_first_arena_v1 import run_root_pool_game_v1

    assert _resolve_runner_v1(
        "scripts.run_performance_first_arena_v1:run_root_pool_game_v1"
    ) is run_root_pool_game_v1


def test_build_wave6_arena_games_binds_checkpoint_and_same_pool() -> None:
    from scripts.run_performance_first_arena_v1 import build_wave6_arena_games

    checkpoint = Path(
        "runs/meta-specialist-v4-archaludon-longrun-wave6-current/"
        "archaludon-training-checkpoints/seed-0/best-recurrent-bc-v4.pt"
    )
    games = build_wave6_arena_games(
        opponent_ids=("public_archaludon_cinderace_r7",),
        checkpoint=checkpoint,
        subject_deck=Path("opponents/public_archaludon_cinderace_r7/deck.csv"),
        games_per_seat=1,
        base_seed=220000,
    )
    assert len(games) == 2
    assert all(game.policy_id.startswith("wave6-v4-") for game in games)
    assert all(game.metadata["checkpoint_tensor_sha256"] == "36b6ace02e69849ea476565bd0ef283d206a6786ba2ac0c0040e82d263f3292a" for game in games)
    assert all(game.runner_ref.endswith(":run_wave6_pool_game_v1") for game in games)


def test_performance_arena_parser_exposes_queue_safe_timeout_override() -> None:
    from scripts.run_performance_first_arena_v1 import _parser

    args = _parser().parse_args(
        ["--opponent-ids", "official_random", "--output", "/tmp/arena", "--timeout-seconds", "1200"]
    )
    assert args.timeout_seconds == 1200.0
