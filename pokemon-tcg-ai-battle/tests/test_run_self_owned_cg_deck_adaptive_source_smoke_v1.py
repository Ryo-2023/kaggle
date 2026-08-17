from __future__ import annotations

from pathlib import Path

from scripts.run_self_owned_cg_independent_source_smoke_v1 import build_smoke_games


ROOT = Path(__file__).resolve().parents[1]
STAGED = ROOT / "runs/cg-self-owned-deck-adaptive-v1-20260816/staged"
CANDIDATES = ROOT / "runs/cg-self-owned-deck-adaptive-v1-20260816/packages"


def test_candidate_root_rebinds_smoke_packages_with_runtime() -> None:
    games = build_smoke_games(
        staged_root=STAGED,
        candidate_root=CANDIDATES,
        refs=("tomatomato_archaludon",),
        pool_root=ROOT / "opponents",
        games_per_opponent_seat=1,
    )
    assert games
    assert all((Path(game.metadata["candidate_package_root"]) / "cg").is_dir() for game in games)
