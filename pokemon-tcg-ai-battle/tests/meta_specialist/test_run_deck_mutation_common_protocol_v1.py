from pathlib import Path

from scripts.run_deck_mutation_common_protocol_v1 import build_common_protocol_games_v1


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "runs/final-sprint-autonomous/deck-mutation-plamen-v1/candidates.json"
CONFIG = ROOT / "configs/meta_specialist/performance_first_broad_pool_v1.json"
POOL = ROOT / "opponents"
CANDIDATE = "aab824462a561b8a459fc71e1a780dc46487f8ab9ed27514a2dfff17fb40b6d9"


def test_common_protocol_keeps_all_24_references_for_both_arms():
    games = build_common_protocol_games_v1(
        manifest_path=MANIFEST,
        candidate_id=CANDIDATE,
        reference_config=CONFIG,
        pool_root=POOL,
        games_per_opponent_seat=1,
    )
    assert len(games) == 96
    by_arm = {arm: [game for game in games if game.metadata["comparison_arm"] == arm] for arm in ("candidate", "native")}
    assert {key: len(value) for key, value in by_arm.items()} == {"candidate": 48, "native": 48}
    for arm_games in by_arm.values():
        assert {game.opponent_id for game in arm_games} == set(__import__("json").loads(CONFIG.read_text())["opponent_ids"])
        assert {game.seat for game in arm_games} == {0, 1}
        assert all(game.metadata["common_protocol"] is True for game in arm_games)


def test_candidate_and_parent_use_distinct_decks_but_same_policy():
    games = build_common_protocol_games_v1(
        manifest_path=MANIFEST,
        candidate_id=CANDIDATE,
        reference_config=CONFIG,
        pool_root=POOL,
        games_per_opponent_seat=1,
    )
    candidate = next(game for game in games if game.metadata["comparison_arm"] == "candidate")
    native = next(game for game in games if game.metadata["comparison_arm"] == "native")
    assert candidate.policy_sha256 == native.policy_sha256
    assert candidate.deck_sha256 != native.deck_sha256
    assert candidate.opponent_id == native.opponent_id
