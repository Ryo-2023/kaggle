from __future__ import annotations

from pathlib import Path

from scripts.run_cg_public_state_mix_candidate_screen_v1 import (
    _paired_candidate_results,
    build_screen_games,
)
from scripts.run_cg_p1_cem_v1 import _package_manifest_sha


ROOT = Path(__file__).resolve().parents[2]
POOL = ROOT / "runs/cg-p1-public-state-mix-epoch1-20260816-promoted"
SPLIT = POOL / "cg_self_owned_weekend_split.json"
CONTROL = ROOT / "runs/final-sprint-autonomous/cg-policy-screen-v1-retry-safe4-20260814/candidates/cg-lethal-target-v1/package"


def test_build_screen_games_binds_all_candidates_to_train_only_pairs() -> None:
    games, candidate_ids, control_id = build_screen_games(
        split_path=SPLIT,
        pool_root=POOL,
        control_package=CONTROL,
        base_seed=20260816802,
        games_per_opponent_seat=1,
    )
    assert len(candidate_ids) == 6
    assert control_id == "cg-lethal-target-v1-control"
    assert len(games) == 6 * 4 * 2 * 1 * 2
    assert {game.metadata["split"] for game in games} == {"META_TRAIN_PUBLIC_CANDIDATE_SCREEN"}
    assert {game.metadata["arm_role"] for game in games} == {"candidate", "p1_control"}
    assert all(game.metadata["training_exposure"] == 0 for game in games)
    assert all(game.metadata["pool_root"] == str(POOL.resolve()) for game in games)


def test_candidate_delta_uses_the_candidate_block_control_not_global_control() -> None:
    rows = [
        {"block_id": "b0", "policy_id": "candidate-a", "outcome": "win", "seat": 0},
        {"block_id": "b0", "policy_id": "candidate-a", "outcome": "loss", "seat": 1},
        {"block_id": "b0", "policy_id": "control", "outcome": "loss", "seat": 0},
        {"block_id": "b0", "policy_id": "control", "outcome": "loss", "seat": 1},
        {"block_id": "b1", "policy_id": "candidate-b", "outcome": "loss", "seat": 0},
        {"block_id": "b1", "policy_id": "candidate-b", "outcome": "loss", "seat": 1},
        {"block_id": "b1", "policy_id": "control", "outcome": "win", "seat": 0},
        {"block_id": "b1", "policy_id": "control", "outcome": "win", "seat": 1},
    ]
    results = _paired_candidate_results(rows, candidate_ids=("candidate-a", "candidate-b"), control_id="control")
    assert [round(float(result["delta_points"]), 3) for result in results] == [50.0, -100.0]


def test_self_owned_package_manifest_is_bound_into_game_provenance() -> None:
    package = POOL / "self-owned-cg-self-owned-cg-public-state-mix-ahead-lethal-conserve-dfa7f52ed201"
    manifest = package / "self_owned_cg_package_manifest.json"
    assert _package_manifest_sha(package) == __import__("hashlib").sha256(manifest.read_bytes()).hexdigest()
