from __future__ import annotations

from pathlib import Path

from scripts.run_self_owned_cg_independent_source_smoke_v1 import (
    build_smoke_games,
    run_smoke_v1,
)


ROOT = Path(__file__).resolve().parents[1]
STAGED = ROOT / "runs/cg-self-owned-independent-root-policy-family-v1-20260816/staged"


def test_independent_source_smoke_builds_both_seat_games() -> None:
    games = build_smoke_games(
        staged_root=STAGED,
        refs=("aristophanivan_multiply",),
        pool_root=ROOT / "opponents",
        base_seed=2026081901,
    )
    assert len(games) == 16
    assert len({game.metadata["arm_id"] for game in games}) == 8
    assert {game.seat for game in games} == {0, 1}


def test_independent_source_smoke_requires_execute(tmp_path: Path) -> None:
    result = run_smoke_v1(
        staged_root=STAGED,
        output=tmp_path / "smoke",
        refs=("aristophanivan_multiply",),
        execute=False,
    )
    assert result["status"] == "BLOCKED_EXECUTE_REQUIRED"

